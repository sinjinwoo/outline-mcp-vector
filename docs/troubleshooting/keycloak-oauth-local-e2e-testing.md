## 1. 문제 요약

* `MCP_OAUTH_ENABLED` 기반 Keycloak 인증을 구현한 뒤, 실제로 "로컬 개발 MCP 서버 + 홈서버 Keycloak + Claude(claude.ai/Claude Desktop) 원격 커넥터" 조합으로 브라우저 기반 OAuth 로그인까지 끝까지 붙여보는 과정에서 총 다섯 단계에 걸쳐 서로 다른 원인으로 막힘.
* `client_credentials`(curl) 테스트는 이미 전부 통과한 상태였는데도, 실제 브라우저 로그인 플로우에서는 그때 드러나지 않았던 문제들이 순서대로 튀어나왔음.

---

## 2. 원인

다섯 가지가 순서대로 겹쳐 있었음 (하나씩 고칠 때마다 다음 단계에서 새 에러가 남):

1. **Audience 매퍼의 값 필드가 비어있었음**: Keycloak 콘솔에서 Audience 매퍼를 추가할 때 `Included Client Audience`/`Included Custom Audience`를 채우지 않고 저장하면, 저장 자체는 성공하지만 실제로는 `aud` 클레임에 아무것도 안 넣어준다. 발급된 토큰을 디코드해보기 전까진 겉보기엔 정상 설정처럼 보여서 두 번(로컬 Keycloak, 홈서버 Keycloak 각각에서) 똑같이 반복됨.
2. **`MCP_OAUTH_RESOURCE_URL`이 `localhost`를 가리키고 있었음**: 이 값은 401 응답의 `WWW-Authenticate` 헤더에 실려서 "이 주소로 가서 인증 방법을 알아내라"고 클라이언트에게 알려주는 용도인데, 실제 요청을 보내는 클라이언트(Claude)는 Anthropic 클라우드에서 돌기 때문에 `localhost`는 사용자 컴퓨터가 아니라 Anthropic 자기 자신을 가리키게 됨. 그 결과 OAuth 디스커버리가 조용히 실패하고, Claude는 최후의 수단으로 자신이 실제로 접속한 호스트(ngrok 터널 주소)에 `/authorize`를 그냥 찍어봄 → 우리 서버엔 그런 라우트가 없으니 404.
3. **`docker compose restart`는 `.env`를 다시 읽지 않음**: `MCP_OAUTH_RESOURCE_URL`을 고친 뒤 `docker compose restart`로 반영을 시도했지만, 이 명령은 이미 생성된 컨테이너를 그대로 재시작할 뿐 `env_file`을 다시 평가하지 않는다. 컨테이너 안에서 확인해보니 여전히 예전 값이 남아있었음 — `docker compose up -d`(재생성)가 필요했음.
4. **Keycloak client에 Standard flow/Valid redirect URI가 없었음**: 이 client는 애초에 `client_credentials`(서비스 계정) 테스트용으로만 설정해뒀어서 브라우저 로그인에 필요한 설정이 비어있었음. 사용자가 실제로 로그인 화면에서 로그인을 완료한 *후에* Keycloak이 "이 리다이렉트 주소로는 못 돌려보낸다"며 거부(`Invalid parameter: redirect_uri`) — 로그인 실패처럼 보이지만 실제로는 클라이언트 설정 문제였음.
5. **`master` realm의 admin 계정으로 새 realm에 로그인 시도**: Keycloak realm은 사용자 저장소가 완전히 분리되어 있어서, 콘솔 로그인에 쓰는 `master` realm의 admin 계정은 새로 만든 realm(`mcp-realm`)에는 존재하지 않는 별개의 정체성이다. 아이디/비밀번호가 그냥 틀렸다는 에러만 보고는 원인을 바로 알기 어려웠음.

---

## 3. 해결 과정

* curl로 직접 `/.well-known/oauth-protected-resource`, Keycloak의 `/.well-known/openid-configuration` 및 `oauth-authorization-server`, 그리고 문제가 된 `/authorize` 경로를 하나씩 쳐보면서 "어느 단계에서 무엇을 반환하는지"를 실측 — 브라우저로 눈에 보이는 최종 에러 화면 하나만으로는 다섯 가지 원인 중 어느 것인지 구분이 안 됐음.
* Keycloak Admin REST API로 클라이언트의 실제 protocol-mapper 설정(`config` 안의 `included.client.audience` 키 존재 여부)을 직접 조회해서, 콘솔 UI에서 "저장 완료"로 보였던 매퍼가 실제로는 audience 값이 비어있다는 걸 확인.
* 컨테이너 안에서 `python -c "import mcpserver.main as m; print(m.MCP_OAUTH_RESOURCE_URL)"` 식으로 실제로 프로세스가 어떤 env 값을 들고 있는지 직접 확인 — `.env` 파일을 고쳤다는 사실과 "실행 중인 프로세스가 그 값을 실제로 반영했다"는 사실은 별개라는 걸 재확인 (docker-compose 컨테이너 재시작 vs 재생성 차이).
* 각 단계마다 에러 메시지가 이전 단계와 달라지는 걸 보고 "진행은 되고 있다"는 걸 확인하며 순서대로 다음 원인을 좁혀나감.

---

## 4. 배운 점

* **curl로 하는 `client_credentials` 검증과 실제 브라우저 기반 Authorization Code 플로우 검증은 서로 다른 것을 증명한다.** 전자는 서명/발급자/audience 검증 로직만 확인하고, 후자에서만 드러나는 설정(redirect URI, Standard flow, 실사용자 계정, 리소스 URL의 실제 도달 가능성)이 따로 있다 — 하나가 통과했다고 다른 하나도 통과한다고 가정하면 안 됨.
* **"이 서버가 나 자신을 어떤 주소라고 광고하는가"(`MCP_OAUTH_RESOURCE_URL`)는 그 값을 소비하는 쪽이 실제로 어디서 실행되는지를 기준으로 정해야 한다.** 로컬 개발 편의로 넣어둔 `localhost` 기본값은 curl(내 컴퓨터에서 보내는 요청)로는 문제가 안 되지만, 원격 클라이언트(Claude, 클라우드에서 실행)에게는 완전히 무의미한 주소다.
* **Keycloak의 realm은 사용자 저장소까지 포함해 완전히 격리된 네임스페이스다.** `master` realm의 admin 계정, 다른 realm의 client/service-account 사용자는 서로 넘나들 수 없다 — 새 realm을 팔 때마다 그 realm 전용 사용자를 새로 만들어야 한다는 걸 당연하게 여기지 말 것.
* **`docker compose restart` ≠ 설정 반영.** env_file 기반 설정을 바꿨다면 `up -d`(재생성)까지 해야 실제 프로세스에 반영된다 — 이건 이번 프로젝트의 다른 서비스에도 똑같이 적용되는 일반 원칙.

---

## 5. 후속 조치

* [x] `docs/keycloak-reference-compose.yml`, `README.md`, `README.ko.md`의 Keycloak 설정 가이드에 이번에 겪은 다섯 가지를 전부 단계별로 반영 (realm 생성 시 사용자+Credentials 필요, client의 Standard flow/Valid redirect URIs, `MCP_OAUTH_RESOURCE_URL`은 클라이언트가 실제로 도달 가능한 주소여야 함, `docker compose up -d` vs `restart`).
* [ ] ngrok 무료 터널은 재시작마다 주소가 바뀌어서 매번 `.env`를 손으로 맞춰야 하는 게 번거로움 — 자주 로컬 개발 환경에서 이 플로우를 테스트한다면 고정 서브도메인(ngrok 유료 플랜, 또는 Cloudflare Tunnel의 고정 호스트명)을 고려.

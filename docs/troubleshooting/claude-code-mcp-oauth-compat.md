## 1. 문제 요약

* Claude Desktop 원격 커넥터로는 이미 정상 동작 확인된 Keycloak 기반 MCP OAuth 인증(`docs/troubleshooting/keycloak-oauth-local-e2e-testing.md` 참고)을, 같은 서버에 Claude Code(CLI)로 붙이자 실패함.
* 서로 다른 두 단계에서 각각 다른 원인으로 막혔음 — 하나를 고치면 다음 단계에서 새 에러가 나오는 패턴이 다시 반복됨.

---

## 2. 원인

### 2-1. Protected Resource Metadata 경로 불일치

* RFC 9728은 `.well-known/oauth-protected-resource`를 origin과 리소스 경로 *사이에* 삽입하는 방식을 정석으로 삼는다 (`https://host/mcp` → `https://host/.well-known/oauth-protected-resource/mcp`). 설치된 `mcp` Python SDK(1.28.1)도 이 방식대로 라우트를 등록한다.
* Claude Code는 대신 리소스 경로 *뒤에* well-known을 붙이는 방식(`https://host/mcp/.well-known/oauth-protected-resource`)만 시도하고, SDK가 실제로 서빙하는 경로로 폴백하지 않는다. 그 결과 디스커버리 자체가 404로 실패.
* Claude Desktop은 이 문제가 없었음 — 즉 두 클라이언트가 같은 스펙을 서로 다르게 해석/구현하고 있고, 하나(Claude Desktop)로 검증됐다고 다른 클라이언트에서도 통과한다는 보장이 없음.

### 2-2. Keycloak의 "Trusted Hosts" Client Registration Policy

* 위 경로 문제를 고치고 나니 다음 에러로 넘어감: `Policy 'Trusted Hosts' rejected request to client-registration service. Details: Host not trusted.`
* 처음엔 Claude Code 자체의 보안 정책인 줄 알았으나, 실제로는 **Keycloak 쪽 Client Registration Policy**임. Keycloak은 기본적으로 익명 Dynamic Client Registration(RFC 7591)을 허용된 호스트에서 오는 요청으로만 제한하는 "Trusted Hosts" 정책을 realm에 기본 포함하고 있어서, 등록되지 않은 호스트에서의 DCR 시도는 전부 거부됨.
* ngrok처럼 재시작마다 호스트가 바뀌는 개발 환경에서는 이 정책에 매번 새 호스트를 추가하는 게 비현실적.

### 2-3. Claude Code와 Claude Desktop의 OAuth 콜백 방식이 다름

* DCR을 포기하고 사전 등록된 client_id/secret 방식으로 우회하려 할 때 놓치기 쉬운 부분: **Claude Code는 로컬 루프백 콜백(`http://localhost:PORT/callback`)을 쓰고, Claude Desktop/claude.ai는 `https://claude.ai/api/mcp/auth_callback`을 쓴다.** 같은 Keycloak client를 두 클라이언트 모두에 쓰려면 둘 다 Valid Redirect URIs에 등록해야 하는데, Claude Desktop용만 등록해뒀다면 Claude Code 쪽에서 다시 "Invalid redirect_uri"로 막혔을 것.

---

## 3. 해결 과정

* FastMCP의 `@mcp.custom_route(...)` 데코레이터로, Claude Code가 실제로 요청하는 경로(`{streamable_http_path}/.well-known/oauth-protected-resource`, 기본값 기준 `/mcp/.well-known/oauth-protected-resource`)에 SDK가 이미 서빙 중인 것과 동일한 metadata JSON을 추가로 등록 (`mcpserver/main.py`, `MCP_OAUTH_ENABLED`일 때만). SDK 버전에 따라 정석 경로가 bare-root였다가(구버전) `/mcp` 접미사 방식(신버전)으로 바뀌는 것도 함께 확인 — 그래서 아예 두 경로 다 신경 쓰지 않고 Claude Code가 원하는 정확한 경로를 명시적으로 얹는 방식을 택함.
* Keycloak 콘솔에서 클라이언트 트러블슈팅용 문의 대신, Anthropic 공식 문서(`code.claude.com/docs/en/mcp-servers`)를 확인해 Claude Code의 `claude mcp add-json ... --oauth.clientId ... --client-secret` 사전 등록 흐름과 `--callback-port` 옵션을 확인 — DCR을 완전히 우회할 수 있음을 확인.
* Keycloak 클라이언트의 Valid Redirect URIs에 `http://localhost:8080/callback`을 Claude Desktop의 콜백과 나란히 추가.
* `claude mcp remove` 후 `claude mcp add-json '{"type":"http","url":"...","oauth":{"clientId":"...","callbackPort":8080}}' --client-secret`으로 재등록, 시크릿 입력 후 `/mcp` 브라우저 로그인으로 최종 연결 성공 확인.

---

## 4. 배운 점

* **MCP 생태계의 클라이언트들은 아직 스펙의 세부 해석이 완전히 통일돼 있지 않다.** 같은 RFC 9728을 두고도 well-known 세그먼트를 리소스 경로 앞/뒤 어디에 넣을지가 클라이언트마다 다를 수 있다 — 하나의 레퍼런스 클라이언트(Claude Desktop)로 통과했다고 "스펙을 지킨다"고 결론 내리면 안 되고, 지원하려는 클라이언트별로 실제 요청 로그를 떠서 확인해야 한다.
* **에러 메시지의 주체를 성급히 판단하지 말 것.** "Policy 'Trusted Hosts'..." 라는 문구만 보면 Claude Code 자체의 정책처럼 보이지만, 실제로는 다운스트림 인가서버(Keycloak)의 기본 보안 정책이었다 — 어느 서비스가 실제로 그 요청을 처리/거부하는지부터 확인해야 헛다리 짚지 않는다.
* **데스크톱/웹 클라이언트와 CLI 클라이언트는 OAuth 콜백 메커니즘 자체가 다를 수 있다** (브라우저 리다이렉트 vs 로컬 루프백 서버). 여러 클라이언트를 같은 OAuth client 설정으로 지원하려면 각 클라이언트의 redirect URI를 전부 등록해야 한다.
* 여러 클라이언트를 대상으로 하는 서버라면, 표준을 하나 골라 구현하는 것만으론 부족하고 실제 목표 클라이언트마다 최소 한 번씩은 끝까지 붙여봐야 한다는 게 이번에도 재확인됨 (`docs/troubleshooting/mcp-sse-transport-deprecated.md`에서 이미 한 번 배운 교훈과 같은 종류).

---

## 5. 후속 조치

* [x] `mcpserver/main.py`: `MCP_OAUTH_ENABLED`일 때 `/mcp/.well-known/oauth-protected-resource`(Claude Code가 실제로 요청하는 경로)에 protected-resource metadata를 추가로 서빙하는 커스텀 라우트 추가.
* [x] README(한/영): Claude Code 연동 섹션 추가 (사전 등록 client 자격증명 방식, redirect URI 등록, `claude mcp add-json` 명령 예시).
* [ ] 실제 배포 이미지(Docker)에 이 변경사항 반영 후 재빌드 필요 — 로컬 개발 환경에서만 검증됨, 아직 배포는 안 함.
* [ ] `mcp` SDK가 업데이트되어 `/mcp/.well-known/oauth-protected-resource` 경로를 기본 지원하게 되면 이 커스텀 라우트가 더 필요한지 재검토.
* [ ] (Optional) Keycloak Trusted Hosts 정책 쪽에서 안정적인 고정 도메인(ngrok 유료/Cloudflare Tunnel 등)을 쓴다면 DCR 허용 목록에 등록해서 사전 등록 없이도 붙게 하는 대안도 고려 가능.

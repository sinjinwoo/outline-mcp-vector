제공해주신 문제 분석, 원인, 해결 과정, 그리고 공식 문서(Model Context Protocol - Authorization) 내용을 바탕으로 작성하신 정리 문서를 수정 및 보완했습니다.

공식 문서의 핵심인 "FastMCP는 인증 인프라를 직접 구동하지 않고, 외부 인가 서버(예: Keycloak)를 활용하며, 클라이언트가 Bearer 토큰을 요청 헤더에 담아 전송하면 이를 검증하는 역할(Resource Server)만 수행한다"는 점과 "스펙상 쿼리 파라미터 인증은 엄격히 금지된다"는 사실을 명확히 반영하여 기술적 정확도를 높였습니다.

---

# [TROUBLESHOOTING] MCP 서버 인증 스펙 위반 수정 및 OAuth(Keycloak) 전환 계획

## 1. 문제 요약

* **현황**: 현재 MCP 서버 인증(`TokenAuthMiddleware`, `MCP_AUTH_TOKENS`)은 `Authorization: Bearer` 헤더와 `?token=` 쿼리 파라미터 방식을 모두 허용하도록 구현되어 있음.
* **공식 문서 및 스펙 위반 지적**: Claude 공식 커넥터 가이드 및 MCP 인증 표준 사양(Authorization Spec)에 따르면 두 방식 모두 현재 클라이언트 연동 환경에서 지원 대상이 아니거나 스펙 위반임.
1. **정적 Bearer 토큰(Static Bearer) 제한**: Claude의 원격 커넥터 UI는 사용자가 고정된 정적 토큰을 수동으로 붙여넣는 방식을 지원하지 않음. 공식적으로 인가 플로우(OAuth Flow)를 통한 동적 토큰 발급 및 연동만 지원함.
2. **쿼리 파라미터 인증 금지**: MCP 인증 사양(Specification)은 보안상 **URI 쿼리 문자열(`?token=`, `?apiKey=` 등)에 액세스 토큰을 포함하는 것을 명시적으로 금지**함.


* **결과**: 기존의 정적 토큰 및 쿼리 파라미터 기반 인증 방식으로는 Claude 공식 원격 커넥터(Remote Connector)를 통한 서비스 연동이 원천적으로 불가능함. 서버에서 인증을 유지하려면 표준 OAuth 플로우로 전환해야 함.

---

## 2. 원인 분석

* **임시 우회 설계의 부작용**: 이전 이슈(`mcp-sse-endpoint-had-no-auth.md`) 대응 당시, "Claude Desktop의 일부 환경(`url` 전용 필드)에서 커스텀 헤더를 삽입하지 못한다"는 제약을 발견하고 이를 해결하기 위해 쿼리 파라미터(`?token=`) 방식을 Fallback으로 도입했음.
* **스펙 검증 누락**: 일부 클라이언트의 제약을 우회하려는 시도 자체는 작동(curl 테스트 통과)했으나, **MCP 표준 인증 사양에서 쿼리 스트링 인증을 금지한다**는 규범적 제약을 면밀히 확인하지 못함.
* **결과적 고립**: 표준 스펙을 위반하여 우회한 결과, 규격을 엄격하게 준수하는 Claude 공식 원격 커넥터 인프라에서는 해당 서버를 신뢰할 수 없는(Unauthorized) 상태로 간주하여 차단하게 됨.

---

## 3. 해결 방향 및 아키텍처 수립

MCP 공식 문서의 인증 가이드(Authorization)에 따라, 서버가 자체적으로 토큰을 발급하고 관리하는 복잡한 인프라를 구축하는 대신, 표준 외부 인가 서버(Authorization Server)를 활용하고 MCP 서버는 **자원 서버(Resource Server)** 역할만 수행하도록 구조를 개편함.

```
[Claude Client / Connector] --(1. OAuth Flow)--> [Keycloak (Auth Server)]
             |                                             |
     (2. Request with Bearer Token)                (3. JWKS / Token Verification)
             |                                             |
             v                                             v
     [FastMCP Server] <------------------------------------+

```

### 핵심 아키텍처 개편안

1. **외부 인가 서버 도입**: `docker-compose.yml`에 **Keycloak** 컨테이너를 추가하여 Realm, Client, User 및 접근 제어(Access Control) 관리를 전담시킴.
2. **FastMCP 표준 네이티브 훅 활용**: 설치된 `mcp` SDK(1.28+)의 `FastMCP`가 제공하는 표준 인증 검증 인터페이스를 활용함.
* `FastMCP(auth=AuthSettings(...), token_verifier=<TokenVerifier 구현체>)` 설정을 도입.
* 이를 통해 클라이언트가 표준 `Authorization: Bearer <token>` 헤더로 요청을 보낼 때만 접근을 허용함.


3. **토큰 검증(Resource Server 역할)**: 기존의 커스텀 미들웨어(`TokenAuthMiddleware`)와 정적 토큰 풀(`MCP_AUTH_TOKENS`)을 전면 폐기함. 대신 Keycloak의 **JWKS(JSON Web Key Sets) 엔드포인트**를 호출하여, 들어오는 Access Token의 서명 유효성과 만료 여부를 실시간/캐싱 검증하는 `TokenVerifier`를 구현함.

---

## 4. 배운 점 (Lessons Learned)

* **스펙 우회 설계의 위험성**: "클라이언트 환경이 지원하지 않아서 임시로 우회한다"는 판단은 단기적인 작동을 보장할지 몰라도, 표준 규격(Spec)을 위반하는 순간 대규모 플랫폼(Claude 공식 인프라 등)과의 생태계 결합도를 무너뜨린다는 점을 재확인함.
* **"작동성"과 "적합성"의 분리**: 이전 작업에서 curl을 통해 `200 OK` 응답을 확인했더라도 그것이 '표준에 적합하다'는 것을 의미하지는 않음. [[mcp-sse-transport-deprecated]] 사례와 마찬가지로, 기술 도입 시 "실제 통신이 되는가"보다 "표준 명세서(Spec)가 이를 허용하는가"를 먼저 추적해야 유산 코드(Legacy) 생산을 막을 수 있음.
* **표준 프로토콜로의 수렴**: 프로젝트 초기에는 가볍고 단순한 `Static Token Pool` 방식이 효율적이어 보이지만, 실제 운영 환경 및 공식 클라이언트 생태계와 연동되는 시점에는 결국 글로벌 표준인 OAuth / OIDC 메커니즘으로 수렴하게 됨을 경험함.

---

## 5. 후속 조치 계획 (내일 작업 예정)

* [x] **인프라 구축**: 실제 운영 Keycloak을 이 프로젝트의 `docker-compose.yml`에 함께 묶지 않기로 결정 — 사용자가 지정한 방향(README 작업 시 확인)에 따라 참고용 단독 compose 파일(`docs/keycloak-reference-compose.yml`)만 제공. 실제 Keycloak 운영/Realm 설정은 사용자 책임.
* [x] **애플리케이션 수정**: `mcpserver/main.py`에서 기존 `TokenAuthMiddleware` 및 `MCP_AUTH_TOKENS` 로직 제거.
* [x] **네이티브 인증 연동**: `FastMCP(auth=AuthSettings(...), token_verifier=KeycloakTokenVerifier(...))`로 교체. `MCP_OAUTH_ENABLED`(기본 false)로 켜고 끌 수 있게 하고, 꺼져 있으면 완전 개방(인증 없음)으로 기동 — 정적 토큰 폴백은 두지 않기로 함(사용자 결정).
* [x] **설정 및 문서 전면 개정**: `.env.example`, README(한/영), `CLAUDE.md`에서 정적 토큰 설정을 삭제하고 Keycloak OAuth 연동(`MCP_OAUTH_ISSUER_URL`/`MCP_OAUTH_RESOURCE_URL`/`MCP_OAUTH_AUDIENCE`/`MCP_OAUTH_JWKS_URL`) 가이드로 업데이트.
* [ ] **연동 테스트**: 실제 Keycloak을 띄우고 Claude 커넥터에서 OAuth 프로바이더 등록 후 엔드투엔드(E2E) 검증 — 아직 로컬 유닛 테스트(가짜 JWKS + 직접 서명한 JWT)로만 검증했고, 실제 Keycloak/Claude Desktop 조합으로는 안 해봄.
* [x] **테스트 코드 정비**: `tests/test_mcp_auth.py`를 재작성 — 정적 토큰 테스트 제거, OAuth 비활성 시 완전 개방 검증 + RSA 키쌍으로 서명한 JWT를 이용한 서명/audience/만료 검증 케이스 추가.
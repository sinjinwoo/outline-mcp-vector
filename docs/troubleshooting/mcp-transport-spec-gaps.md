> 참고: [MCP Specification 2025-11-25 — Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)

## 1. 문제 요약

* 위 스펙 문서를 항목별로 `mcpserver/main.py` 구현과 대조함.
* **이미 스펙을 지키고 있는 부분** (설치된 `mcp` SDK 1.28.1이 자체 구현하고 있어서 별도 코드 없이 통과):
  * 단일 `/mcp` 엔드포인트로 POST/GET 처리 (Streamable HTTP), 레거시 `/sse`+`/messages`는 이미 제거함 — [[mcp-sse-transport-deprecated]]
  * `Mcp-Session-Id` 발급/검증 (`mcp/server/streamable_http.py`)
  * `MCP-Protocol-Version` 헤더 검증, 미지원 버전이면 400 응답 (`_validate_protocol_version`)
* **비어 있는 부분 (스펙이 MUST로 명시)**: Origin/Host 헤더 검증(DNS rebinding 방지).
  * SDK는 `TransportSecuritySettings`(`mcp/server/transport_security.py`)로 이 기능을 이미 구현해두고 있지만, `FastMCP.__init__`은 **host가 loopback(`127.0.0.1`/`localhost`/`::1`)일 때만** 자동으로 켜준다 (`mcp/server/fastmcp/server.py:177-183`).
  * 이 프로젝트는 프로덕션에서 `MCP_HOST=0.0.0.0`(컨테이너 기본값)으로 뜨기 때문에 `transport_security`가 계속 `None`으로 넘어가고, `TransportSecurityMiddleware`는 `settings=None`이면 `enable_dns_rebinding_protection=False`로 스스로를 초기화한다 (`transport_security.py:40-43`). 결과적으로 **Host 헤더 검증과 Origin 헤더 검증이 프로덕션에서 완전히 꺼져 있음** — 지금 있는 보호는 `TokenAuthMiddleware`의 토큰 체크와 SDK가 항상 켜두는 Content-Type 체크뿐.
* 쿼리 파라미터 인증(`?token=`)이 스펙 위반이라는 점은 이미 별도 문서에서 다루고 있고 Keycloak/OAuth 전환 계획도 잡혀 있음 — 이 문서와는 별개 트랙이 아니라 같이 진행할 항목 → [[mcp-query-param-auth-not-spec-compliant]]

### Lifecycle 스펙(초기화/운영/종료) 대조 결과

* **Initialization / capability negotiation**: `initialize` ↔ `initialized` 핸드셰이크, 프로토콜 버전 협상은 전부 `mcp` SDK(FastMCP)가 처리하고 이 프로젝트는 커스텀 코드를 얹지 않았음. `search_knowledge` 툴 하나만 등록했으니 서버가 광고하는 capability도 `tools`뿐이라 협상 실패/불일치가 날 여지가 없음 — **이 부분은 이미 스펙 준수, 조치 불필요**.
* **Shutdown**: HTTP 트랜스포트의 종료는 "HTTP 커넥션을 닫는 것"으로 정의되어 있고, 별도 종료 메시지가 없음. `supervisord.conf`가 `mcp_server` 프로세스에 보내는 `SIGTERM`은 uvicorn이 기본으로 처리(진행 중인 요청 마무리 후 종료)하므로 **추가 구현 불필요** — 다만 실제로 진행 중인 SSE 스트림이 있을 때 정상 종료되는지는 아직 실측 안 해봄.
* **Timeouts (SHOULD, 실제 갭)**: 스펙은 "요청을 보내는 쪽은 모든 요청에 타임아웃을 둬야 한다"고 명시함. `search_knowledge` 툴 핸들러가 내부적으로 호출하는 두 외부 호출 — `shared/embedder.py`의 `genai.Client(...).models.embed_content(...)`와 `shared/vector_store.py`의 `QdrantClient(url=...)` — 둘 다 **타임아웃을 명시적으로 지정하지 않음** (SDK 기본값에 맡김). Gemini나 Qdrant 쪽이 응답 없이 멈추면 그 MCP 요청 자체가 무한정 걸려있게 되고, 서버 쪽에서 이를 취소(cancellation)할 방법이 없음. MCP 프로토콜 레벨의 요청 타임아웃이라기보단 "툴 핸들러 내부 I/O에 타임아웃이 없다"는 인접 리스크지만, 스펙의 타임아웃 권고 취지(끊긴 연결/자원 고갈 방지)와 정확히 맞닿아 있어 같이 정리함.
* **Error handling**: 스펙이 예시로 든 세 가지(프로토콜 버전 불일치, capability 협상 실패, 타임아웃) 중 앞의 둘은 SDK가 이미 처리. 타임아웃은 위 항목이 해결되기 전까지는 "타임아웃 자체가 없어서 에러도 안 남"인 상태.

---

## 2. 원인

* `TokenAuthMiddleware`를 앞단에 씌우는 데만 집중했고, SDK가 기본 제공하는 DNS rebinding 방지 옵션(`transport_security` 파라미터)은 손대지 않았음.
* 로컬에서 `MCP_HOST=127.0.0.1`로 띄워 테스트할 때는 SDK가 자동으로 보호를 켜주기 때문에 문제가 드러나지 않고, 실제 배포 바인딩(`0.0.0.0`)에서만 조용히 꺼진다는 사실을 SDK 소스를 열어보기 전까지 몰랐음.
* "curl로 토큰 체크가 401/200으로 잘 도는가"만 검증했지, 스펙의 MUST 항목을 하나씩 배포 설정에 대입해서 다시 확인하지는 않았음 — [[mcp-sse-transport-deprecated]]에서도 지적했던 것과 같은 패턴("응답한다"≠"스펙을 지킨다").

---

## 3. 해결 방향 (초안 — 내일 구현하면서 세부 조정)

* `mcpserver/main.py`의 `FastMCP(...)` 생성자에 `transport_security=TransportSecuritySettings(...)`를 명시적으로 전달:
  * `enable_dns_rebinding_protection=True`
  * `allowed_hosts`: 실제 배포 도메인/포트 (예: `notionmcp.jwlabs.dedyn.io`, 리버스 프록시 뒤라면 프록시가 넘기는 Host 값 기준으로 맞춰야 함)
  * `allowed_origins`: Origin 헤더가 없는 요청은 스펙/SDK 둘 다 "same-origin으로 간주해 통과"시키므로 (`_validate_origin`: `if not origin: return True`), curl·Claude Desktop 원격 커넥터 같은 비-브라우저 클라이언트에는 이 화이트리스트가 영향을 안 준다. 실질적으로 막는 대상은 "악성 웹페이지가 브라우저를 통해 내부망의 이 서버로 DNS rebinding 공격을 시도하는 경우"임 — 이 사실을 인지하고 목록을 구성할 것.
  * 값을 하드코딩하지 말고 `.env`에 새 항목(`MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` 등)으로 뺄지 검토 — 배포 도메인이 바뀔 수 있음.
* `tests/test_mcp_auth.py`에 잘못된 Host/Origin 헤더로 403(Origin)/421(Host)을 받는 케이스 추가.
* 세션 관리는 현재 기본값(`stateless_http=False`, 즉 상태 유지) 그대로 두는 게 맞는지만 재확인 — `mcp_server`는 supervisord 하에 프로세스 하나로만 뜨므로 지금은 세션 어피니티 문제가 없음. 나중에 `mcp_server`를 여러 레플리카로 스케일하게 되면 `stateless_http=True` 또는 외부 세션 스토어를 다시 검토해야 함 (지금 당장 할 일 아님, 메모만 남김).
* 쿼리 파라미터 인증 제거 + OAuth/Keycloak 전환은 [[mcp-query-param-auth-not-spec-compliant]]의 계획을 그대로 따라가되, `transport_security` 작업과 같은 배포 사이클에 묶어서 진행 (어차피 `mcpserver/main.py`, `.env.example`, README, `CLAUDE.md`를 동시에 건드림).
* `shared/embedder.py`의 `genai.Client(...)` 생성 시 `http_options=types.HttpOptions(timeout=...)`(google-genai SDK가 지원하는 옵션 확인 필요), `shared/vector_store.py`의 `QdrantClient(url=..., api_key=..., timeout=...)`에 명시적 초 단위 타임아웃 추가. 값은 임베딩/검색 각각 실측 후 여유 있게 잡을 것 (너무 짧으면 정상 요청도 끊길 수 있음).
* `mcp_server`가 SIGTERM을 받았을 때 진행 중인 SSE 스트림이 깨끗하게 종료되는지 실측 (uvicorn 기본 graceful shutdown 타임아웃 확인) — 문제 없으면 조치 불필요, 코드 변경 전 확인 차원.

---

## 4. 배운 점

* 의존 중인 SDK가 스펙이 요구하는 보호 기능을 이미 구현해뒀다고 해서 "스펙을 지키고 있다"고 단정할 수 없다 — **그 기능이 기본으로 켜지는 조건**까지 코드를 열어봐야 한다. 여기선 `host` 값(loopback인지 아닌지)에 따라 자동 on/off가 갈렸는데, 로컬 테스트 환경과 프로덕션 배포 환경의 `MCP_HOST` 값이 다르다는 이유만으로 보호 여부가 뒤바뀌었다.
* 스펙 문서를 "이미 다 지켰다"고 표면적으로 판단하지 말고, MUST 항목 하나하나를 실제 배포 설정값(`MCP_HOST=0.0.0.0` 등)에 대입해서 재확인해야 진짜 갭이 보인다.

---

## 5. 후속 조치 (내일 작업 예정)

* [x] `mcpserver/main.py`: `FastMCP(...)`에 `transport_security=TransportSecuritySettings(...)` 추가, `allowed_hosts`/`allowed_origins`를 env(`MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS`)로 분리. 둘 다 비어있으면 `enable_dns_rebinding_protection=False`로 기존과 동일하게 꺼진 채 시작 — 빈 allow-list로 강제 on 시키면 배포 도메인 자신도 421 맞기 때문에 의도적으로 이렇게 묶음.
* [x] `.env.example` / README(한/영) / `CLAUDE.md`에 새 env var 문서화
* [x] `tests/test_mcp_auth.py`: 잘못된 Host 헤더(421) / 잘못된 Origin 헤더(403) 케이스 추가 (+정상 케이스 1개). 구현 중 발견한 부수 버그도 같이 고침 — 모듈 전역 `FastMCP` 인스턴스가 `StreamableHTTPSessionManager`를 최초 1회만 캐싱하고 `.run()`은 인스턴스당 한 번만 허용하는데, 기존 테스트들이 `TestClient(mcp_main.build_app())`를 파일당 여러 번 열면서 두 번째 테스트부터 `RuntimeError`로 전부 깨지고 있었음(이번 세션에서 만든 코드는 아니고 SSE→Streamable HTTP 전환 때 생긴 기존 버그) → `autouse` 픽스처로 매 테스트 전 `mcp._session_manager = None` 리셋해서 해결.
* [ ] [[mcp-query-param-auth-not-spec-compliant]]의 Keycloak/OAuth 전환 작업과 우선순위·일정 조율 (같이 갈지, 이 작업을 먼저 끝낼지) — 코드 작업이 아니라 의사결정이 필요해서 보류 중
* [x] `shared/embedder.py` (Gemini 호출) / `shared/vector_store.py` (Qdrant 호출)에 명시적 타임아웃 추가 (`GEMINI_TIMEOUT_MS` 기본 30000ms, `QDRANT_TIMEOUT_SECONDS` 기본 10초)
* [ ] `mcp_server`의 SIGTERM graceful shutdown 동작 실측 (진행 중 SSE 스트림 있을 때)
* [ ] (Optional, 나중) `mcp_server` 다중 레플리카로 스케일할 계획이 생기면 `stateless_http` 재검토

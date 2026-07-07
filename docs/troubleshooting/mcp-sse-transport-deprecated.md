## 1. 문제 요약

* 실서버(`notionmcp.jwlabs.dedyn.io`)에 배포한 MCP 서버에 curl로는 `/sse` 엔드포인트가 200 OK + 세션 발급까지 정상 확인됐는데, 정작 Claude Desktop에 그 URL을 등록하면 "SSE 사용 중단" 경고가 뜨면서 연결이 안 됨
* 사용자가 직접 지적: "https://notionmcp.jwlabs.dedyn.io/sse?token=... 이거 아닌가? 내서버? ... 근데 안되는디? ... SSE 사용 중단 이라는데? 클로드는?"

---

## 2. 원인

* MCP 프로토콜 자체가 예전 방식인 "HTTP+SSE" transport(`/sse` GET + `/messages` POST, 별도 두 엔드포인트)를 deprecated 처리하고, 최신 클라이언트들은 **Streamable HTTP**(엔드포인트 하나 `/mcp`가 GET/POST/DELETE를 다 처리)를 기본으로 기대함
* `mcpserver/main.py`는 여전히 `mcp.sse_app()`으로 옛날 transport만 서빙하고 있었음 — curl로 `/sse`에 직접 GET을 날리면 여전히 "정상"으로 보이지만(200 + 세션 ID), 그건 transport 자체가 살아있다는 것만 확인한 것이지 Claude Desktop 같은 최신 클라이언트가 그 transport를 실제로 채택해줄지는 별개 문제였음
* 즉, curl 테스트가 "서버가 요청에 응답하는가"만 검증했고 "클라이언트가 이 transport를 받아들이는가"는 검증하지 못해서, 문제를 한 단계 늦게 발견함

---

## 3. 해결 과정

* 설치된 `mcp` SDK(1.28.1)의 `FastMCP`에 이미 `streamable_http_app()` / `run_streamable_http_async()`가 있는 것을 확인 — `sse_app()`과 동일하게 Starlette 앱을 반환하고 `.add_middleware()`도 그대로 지원해서, 기존 `TokenAuthMiddleware`를 그대로 재사용 가능했음
* `mcpserver/main.py`의 `build_app()`을 `mcp.sse_app()` → `mcp.streamable_http_app()`로 교체. 엔드포인트가 `/sse`+`/messages` 두 개에서 `/mcp` 하나로 통합됨
* `TestClient`로 실제 MCP 핸드셰이크(`initialize` JSON-RPC 호출)까지 붙여서 검증 — 토큰 없음/잘못된 토큰은 여전히 401, 올바른 토큰(헤더 또는 `?token=` 쿼리 파라미터)으로는 200과 함께 `serverInfo`가 담긴 정상 응답을 받는 것까지 확인 (이번엔 curl로 엔드포인트만 살아있는지가 아니라 실제 프로토콜 레벨로 검증)
* 외부에 노출하는 URL 경로(README의 Nginx/Claude Desktop 예시, `.env.example`, 문서 전반)를 전부 `/sse` → `/mcp`로 갱신

  ```python
  def build_app():
      if not MCP_AUTH_TOKENS:
          raise RuntimeError(...)
      app = mcp.streamable_http_app()   # was: mcp.sse_app()
      app.add_middleware(TokenAuthMiddleware)
      return app
  ```

---

## 4. 배운 점

* **엔드포인트가 "응답한다"는 것과 "그 프로토콜을 클라이언트가 채택한다"는 것은 다른 층위의 검증이다.** curl로 200 받는 것만으로 "배포 잘 됐다"고 결론 내리면, transport 자체가 클라이언트 쪽에서 지원 종료됐다는 걸 놓칠 수 있다 — 실제 프로토콜 핸드셰이크(이번엔 `initialize` 호출)까지 흉내 내야 진짜 검증이다
* **프로토콜/SDK의 deprecation 공지는 서버 코드가 잘 동작하고 있어도 조용히 사용자 쪽에서만 체감된다.** 서버 로그나 헬스체크에는 아무 이상이 없었고, 클라이언트(Claude Desktop) UI에서만 경고가 떴다 — "서버가 멀쩡히 응답하니 문제없다"고 단정하지 말고, 실제 목표 클라이언트로 한 번은 끝까지 붙여봐야 한다
* MCP처럼 빠르게 바뀌는 생태계에서는 "SSE 전용으로 고정한다"([[mcp-sse-endpoint-had-no-auth]] 참고) 같은 과거 결정이 몇 주 뒤에는 그대로 유효하지 않을 수 있다 — transport 선택은 한 번 정하고 끝나는 게 아니라 계속 재확인해야 하는 대상

---

## 5. 후속 조치 (Optional)

* [x] `mcpserver/main.py`: `mcp.sse_app()` → `mcp.streamable_http_app()`로 교체, `/mcp` 단일 엔드포인트로 통합
* [x] `TokenAuthMiddleware`가 새 transport에서도 동일하게 동작하는지 (헤더/쿼리 파라미터 토큰 모두) 실제 JSON-RPC 핸드셰이크로 검증
* [x] `tests/test_mcp_auth.py`: `/sse` 기반 테스트를 `/mcp` + 실제 `initialize` 호출 기반으로 재작성
* [x] README(영/한), `.env.example`, `CLAUDE.md`, `supervisord.conf`, `Dockerfile`, `docker-compose.yml`의 SSE 언급을 Streamable HTTP로 갱신
* [ ] 실제 운영 서버(`notionmcp.jwlabs.dedyn.io`)에 새 이미지 재배포 후 Claude Desktop에서 `/mcp` URL로 재연결 테스트 아직 안 함
* [ ] 예전 `/sse` URL을 이미 등록해둔 클라이언트가 있다면 `/mcp`로 갱신 안내 필요

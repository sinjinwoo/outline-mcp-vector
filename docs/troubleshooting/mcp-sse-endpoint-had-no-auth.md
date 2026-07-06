## 1. 문제 요약

* `mcpserver/main.py`의 MCP 서버(SSE, 17080번 포트)가 Nginx 리버스 프록시 + HTTPS 뒤에만 있을 뿐, 애플리케이션 레벨 인증이 전혀 없어서 **URL만 아는 누구나** `search_knowledge` 툴을 호출해 지식베이스를 검색할 수 있는 상태였음
* `MCP_TRANSPORT` 환경변수로 transport를 자유롭게 바꿀 수 있게 열려 있어서, 실제로는 SSE만 쓰는데도 굳이 다른 transport로 바뀔 여지가 남아 있었음
* 사용자가 직접 지적해서 발견: "sse 방식만 지원하게 바꾸고, mcp 사용자 인증을 어떻게 처리하지? 다른사람도 막쓰면 어케해?"

  ```python
  # mcpserver/main.py (변경 전)
  if __name__ == "__main__":
      transport = os.getenv("MCP_TRANSPORT", "sse")
      mcp.run(transport=transport)  # 인증 없이 바로 서빙
  ```

---

## 2. 원인

* `FastMCP.run(transport="sse")`는 내부적으로 `sse_app()`을 만들어 곧바로 uvicorn으로 서빙하는 경로라, 미들웨어를 끼워 넣을 훅이 없음 — 애초에 인증을 추가할 자리가 코드 구조상 없었음
* Claude Desktop 같은 클라이언트는 `mcpServers.<name>.url` 필드만 지원하고 커스텀 헤더를 붙일 수단이 없는 경우가 많아서, `Authorization` 헤더만 검증하는 방식으로는 실사용 클라이언트를 못 붙일 수 있다는 점도 같이 고려해야 했음

---

## 3. 해결 과정

* `mcp.run(transport=...)`를 아예 호출하지 않고, `mcp.sse_app()`으로 Starlette 앱을 직접 꺼내서 `TokenAuthMiddleware`로 감싼 뒤 `uvicorn.run()`으로 직접 서빙하도록 변경 — 이 경로가 유일한 진입점이 되므로 자연히 "SSE 전용"이 됨 (transport 선택지 자체를 제거)

  ```python
  def build_app():
      if not MCP_AUTH_TOKENS:
          raise RuntimeError(
              "MCP_AUTH_TOKENS is not set — refusing to start an unauthenticated MCP server."
          )
      app = mcp.sse_app()
      app.add_middleware(TokenAuthMiddleware)
      return app
  ```

* 토큰은 `GOOGLE_API_KEYS`와 같은 컨벤션으로 `MCP_AUTH_TOKENS`에 쉼표로 여러 개 등록 — 클라이언트(사람)별로 토큰을 따로 발급/회수할 수 있게 함
* 헤더를 못 붙이는 클라이언트를 위해 `Authorization: Bearer <token>` 헤더와 `?token=` 쿼리 파라미터 둘 다 허용하도록 미들웨어를 작성
* `MCP_AUTH_TOKENS`가 비어 있으면 `build_app()`이 기동 시점에 `RuntimeError`를 던지도록 해서, "토큰 설정을 깜빡하고 인증 없이 떠버리는" 사고를 원천 차단
* `starlette.testclient.TestClient`로 401/200 케이스를 직접 검증. 다만 실제 `/sse` 라우트는 응답 바디가 끝나지 않는 스트림이라 `TestClient.get()`으로 성공 케이스를 찍으면 그대로 행업됨 — 성공 케이스는 `TokenAuthMiddleware`만 떼어내 더미 200 라우트에 씌운 별도 테스트 앱으로 검증해서 회피 (`tests/test_mcp_auth.py`)

---

## 4. 배운 점

* "리버스 프록시 + HTTPS"는 전송 구간 암호화일 뿐 인증이 아니다 — URL이 새어나가면(로그, 브라우저 히스토리, 캡처 등) 누구나 접근 가능하므로 애플리케이션 레벨 인증은 별도로 있어야 한다
* 프레임워크가 제공하는 최상위 편의 함수(`FastMCP.run()`)가 내부적으로 무엇을 하는지 모르면, "미들웨어를 어디에 끼워야 하는가" 자체가 막힌다 — 한 단계 아래(`sse_app()`)로 내려가서 ASGI 앱을 직접 다루면 표준 Starlette 미들웨어 체계를 그대로 쓸 수 있었음
* 인증 방식을 정할 때 "이론적으로 가장 표준적인 방법"(Bearer 헤더)만 보지 말고 실제 붙일 클라이언트(Claude Desktop의 `url`-only 설정)가 뭘 지원하는지부터 확인해야 한다 — 헤더 전용으로 만들었으면 배포 후에야 "Claude Desktop에서 인증이 안 붙는다"는 문제로 다시 만났을 것
* SSE처럼 응답이 끝나지 않는 엔드포인트는 동기 TestClient로 "성공 케이스"를 직접 찍으면 테스트가 행업된다 — 인증처럼 스트림 시작 전에 끝나는 로직은 미들웨어만 떼어서 별도로 검증하는 게 안전하다

---

## 5. 후속 조치 (Optional)

* [x] `mcpserver/main.py`에 `TokenAuthMiddleware` 추가, `MCP_TRANSPORT` 옵션 제거(SSE 전용으로 고정)
* [x] `MCP_AUTH_TOKENS` 없이는 기동 자체가 실패하도록 `build_app()`에 가드 추가
* [x] `.env.example`, `README.md`에 `MCP_AUTH_TOKENS` 설정법과 Claude Desktop 연동 시 `?token=` 사용법 반영
* [x] `tests/test_mcp_auth.py` 작성 (401/200, 헤더/쿼리파라미터, 토큰 미설정 시 기동 실패)
* [ ] 토큰 회전/만료 정책은 아직 없음 — 토큰 유출 시 `.env` 수정 후 재기동으로만 회수 가능한데, 사용자가 늘어나면 DB 기반 토큰 관리로 옮길지 검토
* [ ] 현재는 토큰 하나만 새면 그 토큰의 클라이언트를 특정할 방법이 없음(누가 어떤 토큰을 쓰는지는 `.env` 주석으로만 관리) — 요청 로그에 사용된 토큰(또는 토큰 식별자)을 남기는 것도 고려

## 1. 문제 요약

* `search_knowledge`가 Qdrant에 색인된 스냅샷만 보고 결과를 리턴하기 때문에, 색인 이후 Outline에서 해당 문서의 접근 권한이 바뀌거나(컬렉션에서 제외됨) 색인에 쓰인 API 키 자체가 무효화돼도, 다음 정기 sync가 돌기 전까지는 이미 권한을 잃은 문서가 계속 검색 결과로 노출될 수 있는 문제
* "여러 사용자가 있을 때 사용자별 API 키로 문서를 가져오면 인가 처리가 자동으로 되는 것 아니냐"는 논의를 하다가, 그 반례로 발견 — 색인 시점엔 맞게 걸러지더라도 그 이후 권한이 바뀌는 경우는 커버가 안 됨
* 특히 API 키가 통째로 무효화되는 경우엔 반대 방향 문제도 있었음: `indexer/tasks.py`의 `run_sync`가 `iter_all_documents()`의 첫 API 호출에서 401로 예외를 던지면, `live_ids`가 아예 계산되지 않아서 105~109줄의 stale 삭제 로직도 실행되지 않고, 이미 색인된 내용이 Qdrant에 무기한 방치되는 비대칭이 있었음

---

## 2. 원인

* 색인은 특정 시점(마지막 sync)의 스냅샷이고, 검색은 그 스냅샷만 봄 — 권한 변경은 sync 주기(`SYNC_INTERVAL_SECONDS`, 기본 1시간)나 웹훅에 의해서만 반영되는데, Outline 웹훅은 "문서 내용이 바뀌었다"는 이벤트지 "멤버십/권한이 바뀌었다"는 이벤트가 아니라서 권한만 바뀐 경우엔 아무 트리거도 없음
* `mcpserver/main.py`의 `search_knowledge`는 원래 `embed_query` → `vector_store.search`만 호출하고 끝 — Outline에 다시 물어보는 과정이 전혀 없었음
* API 키가 완전히 죽는 경우, `run_sync`의 흐름을 보면:

  ```python
  # indexer/tasks.py — 수정 전 run_sync
  live_ids = asyncio.run(_walk_documents())   # 첫 API 호출에서 401 -> 여기서 예외 전파, 아래 줄 도달 못 함
  stale_ids = get_all_doc_ids() - live_ids    # live_ids가 없으니 이 diff도 실행 안 됨
  ```

  "권한이 좁아지는" 경우(문서 몇 개가 목록에서 빠짐)는 다음 sync의 diff에서 자연스럽게 stale 처리되지만, "키 자체가 죽는" 경우는 diff 단계까지 가지도 못해서 오히려 아무것도 정리되지 않는 비대칭이 있었음

---

## 3. 해결 과정

* 처음엔 "sync가 오래 실패 중이면 `/sync/status`를 보고 `search_knowledge` 전체를 차단하자"는 안을 검토했으나, 이건 문서 하나의 권한 문제로 전체 검색을 막는 지나치게 뭉툭한 게이트라 기각
* 대신 "검색 시점에 후보 문서별로 Outline에 다시 물어보자"는 방향으로 전환. 두 가지 조회 방식을 비교:
  * `documents.list`로 "지금 이 키가 볼 수 있는 문서 전체 목록"을 매번 새로 만드는 방식 — Outline API가 페이지당 25개(`_PAGE_LIMIT`)만 내려주기 때문에, 코퍼스가 커질수록(문서 수에 비례해서) 검색 1번의 비용이 계속 늘어남. 채택 안 함
  * 후보 doc_id별로 `documents.info`를 개별 호출하는 방식 — 비용이 검색 결과 개수(K)에만 비례하고 전체 코퍼스 크기와 무관. `connector/outline.py`에 `check_access(doc_id)`를 새로 추가(컬렉션명 해석 등 `get_document`의 부가 작업 없이 raw 상태코드만 확인)
* 실패를 전부 "권한 없음"으로 뭉뚱그리면 위험하다는 걸 확인 — 403/404(그 문서만 못 봄)와 401(키 자체가 죽음)을 구분해야 함. 401을 만나면 나머지 후보 검증을 계속하지 않고 즉시 `RuntimeError`로 단락 처리(`mcpserver/main.py`의 `_check_access`/`_verify_all`/`_verify_until_enough`) — 안 그러면 죽은 키로 벡터DB를 계속 훑으며 폭주할 수 있음
* 후보가 부족할 때 얼마나/어떻게 더 가져올지도 여러 번 조정:
  * "1개씩 늘리기"는 검증이 직렬화돼서(다음 후보가 필요한지는 이전 걸 확인해야 알 수 있으므로) 느려짐 → 기각
  * `limit * 2` 같은 가변 배수도 고려했으나 최종적으로 **고정 40개** 캡으로 확정(`MAX_ACCESS_CHECK_CANDIDATES`)
  * 부족분을 다시 가져올 때 Qdrant의 `offset` 파라미터(`shared/vector_store.py`에 추가)로 이미 검증한 상위 `limit`개를 다시 조회하지 않고 (limit+1)~40등만 가져오도록 최적화
  * 부족분 검증 중 `limit`이 채워지는 순간 나머지 진행 중이던 검증 요청을 취소하도록 개선(`_verify_until_enough`의 `asyncio.wait(..., return_when=FIRST_COMPLETED)` 루프) — 불필요한 Outline 호출을 줄임. 조기 취소로 완료 순서가 뒤섞일 수 있어서 최종 반환 전에 각 후보가 들고 있는 Qdrant `score`로 재정렬
* `/sync/status`는 애초 "검색 차단 게이트" 역할을 시키려 했으나, 그 역할이 라이브 체크로 옮겨가면서 필요 없어짐 — 대신 운영자가 백그라운드 색인 파이프라인이 살아있는지 볼 수 있게 `last_synced_at`만 추가로 노출(`indexer/main.py`, `shared/sync_state.get_last_synced_at()` 재사용)
* `tests/test_outline_connector.py`(`check_access` 200/403/401), `tests/test_search_access_check.py`(전부 통과 / 백필 / 조기취소+재정렬 / 40개 소진 시 부족한 채로 반환 / 401 즉시 단락), `tests/test_sync_status_endpoint.py`(`last_synced_at` 반영) 추가

---

## 4. 배운 점

* 인증(누가 호출할 자격이 있는가)과 인가(그 결과를 볼 자격이 있는가)는 완전히 다른 계층 — Keycloak OAuth(`MCP_OAUTH_ENABLED`)를 켜도 문서 단위 인가가 자동으로 따라오지 않는다. 색인 기반 검색 시스템에서는 특히 "색인 시점 인가"와 "서빙 시점 인가"를 구분해서 생각해야 한다
* "가져올 때"(색인 시점) 인가만으로는 권한이 사후에 좁아지는 경우를 못 잡는다. 그렇다고 서빙 시점 검증을 코퍼스 전체를 훑는 방식(`documents.list`)으로 하면 코퍼스가 커질수록 매 쿼리가 느려지는, 방향이 거꾸로인 설계가 된다 — 검증 비용은 항상 "결과 개수"에 비례하게 만들어야지 "전체 데이터 크기"에 비례하게 만들면 안 된다
* 병렬 처리("한꺼번에 다 확인")와 "필요한 만큼만 하고 멈추기"는 종종 충돌한다. 완전 병렬(`asyncio.gather`)은 빠르지만 남는 요청까지 다 쏘고, 완전 직렬(1개씩)은 낭비가 없지만 느리다. `asyncio.wait(FIRST_COMPLETED)` 기반 조기 취소가 그 중간 지점이고, 대신 완료 순서가 뒤섞이는 대가는 있어서(여기선 `score`로 재정렬해서 해결) 그 트레이드오프를 인지하고 있어야 한다
* 실패를 전부 "안 됨"으로 뭉뚱그리지 말 것 — 일시적 오류(타임아웃/5xx)와 확정된 거부(403/404)와 자격 자체의 무효화(401)를 구분해야, 일시적 장애 한 번이 전체를 폭주시키거나 오탐시키는 걸 막을 수 있다

---

## 5. 후속 조치 (Optional)

* [x] `connector/outline.py`에 `check_access(doc_id)` 추가
* [x] `shared/vector_store.py`의 `search()`에 `offset` 파라미터 추가
* [x] `mcpserver/main.py`의 `search_knowledge`를 2단계 조회 + 조기취소 + 401 단락 + score 재정렬 구조로 재작성
* [x] `indexer/main.py`의 `/sync/status`에 `last_synced_at` 노출
* [x] 단위 테스트 추가 (`tests/test_outline_connector.py`, `tests/test_search_access_check.py`, `tests/test_sync_status_endpoint.py`)
* [ ] 실제 Outline 인스턴스에 대고 문서 권한 회수 / API 키 무효화 시나리오를 수동으로 재현해 403 제외·401 단락이 기대대로 동작하는지 확인 (mock 테스트만으로는 실제 Outline API의 상태코드 규약까지 검증 못 함)
* [ ] `MAX_ACCESS_CHECK_CANDIDATES = 40` 고정값이 실제 코퍼스 크기/권한 분포에서 충분한지 운영 중 관찰

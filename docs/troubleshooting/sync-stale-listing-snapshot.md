## 1. 문제 요약

* `run_sync`(동기화)가 `documents.list` 페이지네이션으로 미리 읽어온 문서 스냅샷을 그대로 인덱싱에 사용하고 있어서, "목록조회 시점"과 "실제 쓰기(락 획득) 시점" 사이에 Outline에서 문서가 다시 수정되면 오래된 내용으로 Qdrant를 덮어쓸 수 있는 문제
* `doc_lock(doc_id)`로 웹훅-동기화 간 동시쓰기 충돌(`webhook-sync-race-condition.md`)을 해결한 뒤, 사용자가 "읽기와 쓰기 사이 시간차 동안 최신 정보가 반영돼도 옛날 데이터를 계속 쓰게 되는 것 아니냐"고 지적하며 추가로 발견

---

## 2. 원인

* `doc_lock`은 "동시에 같은 문서를 쓰지 못하게" 막을 뿐, "쓰는 내용이 최신인지"는 보장하지 않음
* `run_sync`는 페이지당 여러 문서를 순차 처리하는데, 각 문서의 `Document` 객체(본문 포함)를 목록조회 시점에 이미 확보해두고, 한참 뒤 자기 차례가 왔을 때 그 객체를 그대로 인덱싱했음

  ```python
  # indexer/tasks.py — 수정 전
  async for doc in connector.iter_all_documents():
      ...
      with doc_lock(doc.doc_id):
          index_document(doc)  # 목록조회 시점의 스냅샷을 그대로 사용
  ```

* 시나리오: 동기화가 문서 A를 리스팅한 직후(v1), 사용자가 Outline에서 문서 A를 다시 수정(v2)하고 웹훅이 이를 즉시 `get_document`로 재조회해 반영. 그런데 동기화 루프가 뒤늦게 문서 A 차례가 되어 `doc_lock`을 잡으면, 웹훅이 이미 써놓은 v2를 목록조회 때 들고 있던 v1으로 덮어씀
* `doc_lock`은 동시 실행만 직렬화할 뿐 "누가 더 최신 데이터를 들고 있는지"는 신경 쓰지 않으므로, 락 순서와 무관하게 이 역전이 발생할 수 있었음

---

## 3. 해결 과정

* 목록조회 결과는 "재인덱싱이 필요한지 판단"(`updated_at`을 커서와 비교)하는 용도로만 쓰고, 실제 인덱싱은 `doc_lock` 획득 직후 `get_document(doc_id)`로 문서를 다시 조회한 최신 내용으로 수행하도록 변경 — 웹훅 처리 경로(`process_webhook_event`)가 이미 쓰던 "ID로 재조회 후 인덱싱" 원칙을 동기화 경로에도 동일하게 적용

  ```python
  # indexer/tasks.py — 수정 후
  async for doc in connector.iter_all_documents():
      ...
      with doc_lock(doc.doc_id):
          fresh_doc = await connector.get_document(doc.doc_id)
          index_document(fresh_doc)
  ```

* 락이 상호배제를 보장하므로, 나중에 락을 잡는 쪽(동기화든 웹훅이든)이 항상 그 시점 기준 최신 내용을 재조회해서 쓰게 되어, 트리거 순서와 무관하게 최종 상태가 항상 최신으로 수렴
* `tests/test_tasks.py`에 목록조회 스냅샷(제목 "Stale Title")과 재조회 결과(제목 "Fresh Title")를 다르게 준 뒤, 실제로 인덱싱되는 건 재조회 결과여야 한다는 걸 검증하는 테스트 추가 (`test_run_sync_reindexes_freshly_fetched_content_not_listing_snapshot`)
* 이어서 "그럼 애초에 목록조회 때 본문(`text`)까지 받아올 필요가 있냐"는 후속 논의가 나와서, Outline API의 `x-api-version: 3` 헤더로 목록조회에서 본문을 아예 빼고 대역폭을 줄일 수 있는지 검토
  * `outline/outline` 서버 소스(`server/presenters/document.ts`)를 확인한 결과, v3에서는 `text`가 빠지는 대신 `data`(ProseMirror 구조화 JSON)가 기본으로 포함되고, `documents.list`/`documents.info` 라우트 모두 클라이언트가 `includeData: false`를 보내도 이를 프레젠터에 전달하지 않아 끌 방법이 없음을 확인
  * 실제 서버(.env의 `OUTLINE_BASE_URL`)에 `documents.list`를 두 방식으로 호출해 실측: 같은 문서 하나에 대해 `text`는 273자였지만 `data`는 JSON 기준 1972자로 오히려 약 7배 더 큼
  * 이론(소스 코드 분석)과 실측이 일치해, v3 적용은 보류하고 목록조회는 그대로 기본(v0) 헤더 유지

---

## 4. 배운 점

* "동시 실행 방지"(락)와 "데이터 신선도 보장"은 서로 다른 문제 — 락으로 경쟁 상태를 막았다고 해서 오래된 데이터를 쓰는 문제까지 자동으로 해결되지 않음. 여러 비동기 경로(웹훅/시작 시/주기/수동)가 같은 자원을 다룰 때는 "언제 읽었나"와 "언제 썼나" 사이의 시차를 항상 의심해야 함
* 읽기(목록조회/캐시)와 쓰기 사이에 지연이 있는 구조라면, 쓰기 직전에 다시 조회하는 것이 근본적인 해결책 — 판단(무엇을 처리할지)과 실행(무엇을 쓸지)에 쓰는 데이터의 소스를 분리하면 됨
* "이론적으로 더 효율적으로 보이는" API 옵션도, 실제로 무엇이 함께 바뀌는지(v3의 `data` 필드처럼) 소스/실서버로 확인하기 전에는 적용하지 않는 게 안전 — 대역폭을 줄이려던 시도가 오히려 늘릴 수도 있었음

---

## 5. 후속 조치 (Optional)

* [x] `run_sync`가 목록조회 스냅샷 대신 `doc_lock` 획득 후 `get_document`로 재조회한 내용을 인덱싱하도록 수정 (`indexer/tasks.py`)
* [x] 재조회 동작을 검증하는 단위 테스트 추가 (`tests/test_tasks.py`)
* [x] Outline API v3(`x-api-version: 3`)로 목록조회 대역폭을 줄일 수 있는지 검토 — 실측 결과 효과 없음(오히려 커짐)으로 확인, 적용 안 함
* [ ] `full=True` 전체 재동기화 시 재조회로 인해 Outline API 호출이 문서 수만큼 2배가 되는 부분의 실제 운영 영향(레이트리밋 등) 모니터링

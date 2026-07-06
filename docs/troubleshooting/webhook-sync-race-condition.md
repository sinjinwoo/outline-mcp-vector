## 1. 문제 요약

* 웹훅으로 들어온 문서 갱신 처리와, 동기화(서버 시작 시 / 1시간 주기 Beat / 수동 `/sync/outline`)가 **같은 문서**를 동시에 인덱싱하면 둘 중 하나의 결과가 유실될 수 있는 문제
* Celery + Redis로 비동기 처리를 전환하고, 1시간 주기 Beat 스케줄을 추가하는 작업 중 사용자가 직접 지적해서 발견: "1시간 단위로 스케쥴링도 하면 웹훅이랑 동시 발생시에 문제가 생길수도 있지 않나?"
* 발생 조건: 동기화 태스크가 문서 A를 재임베딩하는 도중, 정확히 그 시점에 문서 A의 웹훅 이벤트(수정)가 도착해서 별도 워커 프로세스/스레드가 동시에 같은 `doc_id`를 처리하는 경우

  ```python
  # indexer/pipeline.py — 문서 하나를 인덱싱하는 과정 (원자적이지 않음)
  def index_document(doc: Document) -> None:
      chunks = chunk_markdown(doc.text)
      embeddings = embed_passages(chunks)   # 느림 (외부 API 호출)
      delete_by_doc_id(doc.doc_id)          # 기존 벡터 삭제
      upsert_chunks(doc.doc_id, chunks, embeddings, metadata)  # 새 벡터 upsert
  ```

---

## 2. 원인

* `run_sync`(전체/증분 동기화)에는 **동기화끼리** 중복 실행되지 않도록 막는 전역 락(`acquire_sync_lock`)만 있었고, **문서 단위**로 처리를 직렬화하는 장치는 없었음
* `process_webhook_event`(웹훅 처리)와 `run_sync`는 서로 완전히 독립된 Celery 태스크라서, 같은 `doc_id`를 대상으로 동시에 실행되는 걸 막을 방법이 전혀 없었음
* `index_document`의 내부 흐름이 "임베딩 → 삭제 → upsert" 순서로, **삭제와 upsert 사이에 다른 프로세스가 끼어들 수 있는 구간**이 존재:
  1. Worker A(동기화)가 문서 A의 구버전을 읽어 느린 임베딩을 시작
  2. 그 사이 Worker B(웹훅)가 문서 A의 신버전을 읽어 더 빨리 임베딩을 끝내고 삭제+upsert까지 완료
  3. Worker A가 뒤늦게 임베딩을 끝내고 삭제(B가 upsert한 신버전 벡터까지 지움) 후 자신의 구버전 벡터를 upsert
  4. 결과적으로 Qdrant에는 최신 상태(B)가 아닌 오래된 상태(A)가 남음 — **최신 수정 사항 유실**
* Celery 워커는 기본 prefork 풀로 여러 프로세스가 동시에 태스크를 처리하므로, 이런 인터리빙이 이론적 가능성이 아니라 실제로 발생할 수 있는 상황이었음

---

## 3. 해결 과정

* 사용자 지적을 받고 시나리오를 구체화해서 실제로 데이터 유실이 가능한 레이스인지 확인 (위 1~4번 시퀀스)
* Redis 기반 **문서별 락**(`doc_lock(doc_id)`)을 `indexer/sync_lock.py`에 추가 — 트리거 출처(웹훅/시작/주기/수동)와 무관하게, 같은 `doc_id`에 대한 모든 인덱싱/삭제 호출을 직렬화

  ```python
  @contextmanager
  def doc_lock(doc_id: str):
      lock = _get_client().lock(
          f"outline_rag:doc_lock:{doc_id}",
          timeout=_DOC_LOCK_TIMEOUT_SECONDS,          # 락을 쥔 채로 죽었을 때의 안전장치
          blocking_timeout=_DOC_LOCK_BLOCKING_TIMEOUT_SECONDS,  # 대기 상한
      )
      if not lock.acquire():
          raise TimeoutError(f"Timed out waiting for the index lock on document {doc_id}")
      try:
          yield
      finally:
          lock.release()
  ```

* `indexer/tasks.py`의 `process_webhook_event`(인덱싱/삭제 분기 모두)와 `run_sync`(문서별 루프 안의 인덱싱, 그리고 삭제된 문서 정리 루프)에 전부 적용 — "쓰기 경로는 반드시 `doc_lock`을 거친다"는 규칙으로 통일
* `fakeredis` + 스레드로 재현 테스트 작성: 스레드 A가 `doc_lock("doc-123")`을 잡고 있는 동안 스레드 B를 살짝 늦게 기동시켜, B가 A보다 먼저 시작하지 않는지 검증

  ```python
  def test_doc_lock_serializes_access_to_the_same_document():
      events = []

      def hold_lock(name, hold_seconds):
          with sync_lock.doc_lock("doc-123"):
              events.append(f"{name}-start")
              time.sleep(hold_seconds)
              events.append(f"{name}-end")

      first = threading.Thread(target=hold_lock, args=("A", 0.3))
      second = threading.Thread(target=hold_lock, args=("B", 0.0))
      first.start()
      time.sleep(0.05)  # A가 먼저 락을 잡도록 보장
      second.start()
      first.join(); second.join()

      assert events == ["A-start", "A-end", "B-start", "B-end"]
  ```

* 실행 결과 `["A-start", "A-end", "B-start", "B-end"]`로 정확히 직렬화되는 것을 확인 (B는 A가 락을 놓을 때까지 시작조차 하지 못함)
* 서로 다른 문서(`doc-A`, `doc-B`)는 락이 겹치지 않아 병렬로 처리된다는 것도 별도 테스트로 함께 확인 — 락 범위가 지나치게 넓어 불필요하게 처리량을 떨어뜨리지 않는지도 검증

---

## 4. 배운 점

* "동시 실행 방지"가 필요한 대상은 **하나가 아닐 수 있다** — 이번 경우 "동기화끼리 중복 실행 방지"(전역 락)와 "같은 문서에 대한 동시 쓰기 방지"(문서별 락)는 서로 다른 문제였고, 하나를 막았다고 다른 하나가 자동으로 해결되지 않았음
* 비동기 처리 경로를 여러 개(웹훅 / 시작 시 동기화 / 주기 동기화 / 수동 동기화) 추가할 때마다, "이 경로들이 같은 자원(문서)을 동시에 건드릴 수 있는가?"를 매번 다시 질문해야 한다 — 특히 주기 스케줄러(Beat)처럼 트리거가 늘어날수록 교차 가능성도 늘어남
* "delete 후 upsert" 같은 두 단계로 나뉜 쓰기 작업은 그 자체로 원자적이지 않다는 걸 항상 의심해야 한다. 락으로 감싸지 않으면 두 프로세스의 delete/upsert가 인터리빙되어 최신 데이터가 오래된 데이터에 덮어써질 수 있다.
* 락 관련 버그는 우연히 타이밍이 어긋나지 않으면 재현이 안 되기 때문에, 실제 멀티스레드/멀티프로세스 테스트로 "느린 작업 vs 빠른 작업"의 순서를 인위적으로 만들어서 검증해야 신뢰할 수 있다 (단순 순차 호출 테스트로는 이런 버그를 못 잡음).

---

## 5. 후속 조치 (Optional)

* [x] `indexer/sync_lock.py`에 `doc_lock(doc_id)` 추가
* [x] `process_webhook_event`, `run_sync`의 모든 인덱싱/삭제 호출에 적용
* [x] 스레드 기반 재현 테스트 작성 및 통과 확인 (`tests/test_sync_lock.py`)
* [ ] 락 대기가 `blocking_timeout`(현재 60초)을 초과하면 `TimeoutError`가 발생해 해당 태스크가 실패 처리됨 — Celery 재시도 정책(`max_retries`)이 이 케이스까지 충분히 커버하는지 별도 점검 필요
* [ ] 워커 concurrency를 늘렸을 때(`--concurrency=N`) 동시에 여러 문서를 처리하다가 락 대기가 몰리는 상황(예: 동기화가 수백 개 문서를 한꺼번에 처리 중일 때 웹훅이 몰리는 경우)의 지연 시간도 운영 중 모니터링해볼 것

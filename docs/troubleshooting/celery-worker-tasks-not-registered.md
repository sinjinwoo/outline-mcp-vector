## 1. 문제 요약

* Celery 워커(`celery -A indexer.celery_app worker`)를 띄웠는데 태스크가 하나도 등록되지 않아, 웹훅/동기화 요청을 큐에 넣어도 영원히 처리되지 않는 문제
* 웹훅 비동기 처리를 FastAPI `BackgroundTasks`에서 Celery + Redis로 옮기는 작업 중, 실제 Redis 컨테이너 + 워커를 띄워 통합 테스트를 하다가 발견

  ```text
  [tasks]


  [2026-07-06 15:36:33,875: INFO/MainProcess] Connected to redis://localhost:16379/1
  [2026-07-06 15:36:34,948: INFO/MainProcess] celery@DESKTOP-26979RN ready.
  ```

---

## 2. 원인

* `celery -A indexer.celery_app worker` 커맨드는 `-A`로 지정한 모듈(`indexer/celery_app.py`) 하나만 import한다
* 태스크 정의(`@celery_app.task`)는 별도 파일 `indexer/tasks.py`에 있었는데, `celery_app.py`가 이 모듈을 import하지 않아서 워커 프로세스 입장에서는 태스크가 아예 존재하지 않는 것과 같았음
* FastAPI 프로세스(`indexer/main.py`)는 `from indexer.tasks import process_webhook_event, run_sync`를 직접 import하기 때문에 우연히 같은 문제를 겪지 않았고, 그래서 API 서버만 띄워서는 문제를 눈치채지 못함 — 실제 작업을 수행하는 워커의 진입점(`-A indexer.celery_app`)이 API 서버와 다르다는 걸 놓쳤음
* 처음에 놓쳤던 부분: Celery의 `-A app` 옵션이 태스크를 "알아서" 찾아준다고 생각했음. 실제로는 지정한 모듈만 로드하며, 태스크가 등록되려면 그 모듈이 로드되는 시점에 `@celery_app.task` 데코레이터가 실행돼야 한다.

  ```python
  # indexer/celery_app.py (수정 전)
  celery_app = Celery("indexer", broker=REDIS_URL, backend=REDIS_URL)
  celery_app.conf.update(...)
  # indexer/tasks.py 는 어디에서도 import되지 않음 -> 워커 기동 시 태스크 미등록
  ```

---

## 3. 해결 과정

* Docker로 임시 Redis 컨테이너를 띄우고, 워커를 `--pool=solo`로 기동해 시작 로그를 직접 확인 → `[tasks]` 섹션이 비어 있는 것을 보고 원인 추적 시작
* `indexer/celery_app.py` 맨 아래에 `from indexer import tasks`를 명시적으로 추가. 순환 임포트(`tasks.py`가 `celery_app.py`의 `celery_app`을 다시 import함)를 피하기 위해, `celery_app = Celery(...)` 정의가 끝난 뒤 파일 하단에 배치

  ```python
  # 파일 하단에 배치 — celery_app이 이미 모듈 네임스페이스에 바인딩된 뒤이므로
  # indexer/tasks.py의 `from indexer.celery_app import celery_app`가 안전하게 동작함
  from indexer import tasks  # noqa: F401,E402
  ```

* 워커를 재기동해 `[tasks]`에 `indexer.process_webhook_event`, `indexer.run_sync`가 정상적으로 나열되는 것을 확인
* `process_webhook_event.delay(...)`로 실제 태스크를 큐에 넣고, 워커 로그에서 `received` → `succeeded`까지 end-to-end로 검증 완료

---

## 4. 배운 점

* Celery의 `-A <module>` 옵션은 지정한 모듈만 로드할 뿐, 프로젝트 전체에서 태스크를 자동으로 찾아주지 않는다. 태스크 모듈은 앱이 정의된 모듈에서 명시적으로 import하거나 `celery_app.autodiscover_tasks([...])`를 써야 등록된다.
* 순환 임포트가 걱정되는 구조에서는 "정의가 끝난 뒤"(파일 최하단)에 import하면 우회할 수 있다.
* 코드가 "정상적으로 import된다"는 것과 "실제 배포 진입점에서 정상 동작한다"는 것은 다른 문제다. FastAPI 프로세스는 우연히 `tasks.py`를 import해서 증상이 가려졌지만, 실제 프로덕션 진입점(워커 커맨드)으로 검증하지 않았다면 이 버그는 배포 후에야 발견됐을 것이다. → **모든 프로세스 진입점(API, worker, beat)을 각각 실제로 기동해서 확인하는 습관이 필요하다.**

---

## 5. 후속 조치 (Optional)

* [x] `celery_app.py`에 `indexer.tasks` 명시적 import 추가
* [x] Docker Redis + `--pool=solo` 워커로 태스크 등록/실행 여부 재현 및 재검증
* [ ] CI 또는 배포 스모크 테스트에 `celery -A indexer.celery_app inspect registered`로 필요한 태스크가 다 등록됐는지 확인하는 단계 추가하면 재발 방지에 도움
* [x] `beat` 서비스도 별도 프로세스 진입점이므로, `celery -A indexer.celery_app beat` 기동 후 `celery_app.conf.beat_schedule`에 `periodic-outline-sync` 항목이 정상 로드되는 것까지 확인

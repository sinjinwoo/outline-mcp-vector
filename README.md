# outline-rag-mcp

Outline 문서를 자동으로 벡터화하여 AI 에이전트가 의미 기반 검색을 할 수 있게 해주는 RAG + MCP 서버입니다. Docker Compose 한 줄로 실행됩니다.

## 아키텍처

```
Outline (webhook)
      ↓
  Indexer (FastAPI)   ← 서명 검증 후 Celery 태스크 큐잉, 즉시 200 응답
      ↓
  Redis               ← Celery 브로커 + 동기화/문서 락
      ↓
  Worker (Celery)      ← 문서 수신 → 청킹 → 임베딩 → Qdrant 저장
      ↑
  Beat (Celery)        ← 주기적(기본 1시간) 증분 동기화 트리거
      ↓
  Qdrant               ← 벡터 DB
      ↓
  MCP Server           ← AI 에이전트가 search_knowledge() 호출
```

- **Indexer**: Outline webhook 서명 검증 후 Celery로 작업을 큐잉만 하고 즉시 응답. 실제 처리는 Worker가 담당.
- **Worker**: Celery 워커. 웹훅 이벤트 처리, 시작 시 동기화, 수동/주기 동기화를 모두 여기서 실행 — API 프로세스가 재시작돼도 큐에 남은 작업은 유실되지 않음.
- **Beat**: Celery 스케줄러. `SYNC_INTERVAL_SECONDS`(기본 3600초) 간격으로 증분 동기화를 큐에 등록.
- **Redis**: Celery 브로커 겸 락 저장소. 동기화 중복 실행 방지, 그리고 웹훅과 동기화가 같은 문서를 동시에 건드리는 걸 막는 문서별 락(`doc_lock`)에 사용.
- **MCP Server**: `search_knowledge` 도구를 SSE 방식으로 제공. Claude Desktop 등 MCP 클라이언트에서 연결.
- **임베딩**: Gemini(기본, 다중 API 키 라운드로빈) / OpenAI 중 선택.
- **동기화**: 서버 시작 시 + 웹훅 이벤트 + 주기적(Beat) 트리거, 모두 `updated_at` 기준 증분 처리이며 Outline에서 사라진 문서는 자동으로 Qdrant에서도 제거됩니다.

---

## 빠른 시작

### 1. 환경 변수 설정

```bash
curl -O https://raw.githubusercontent.com/sjw0066/outlineMcp/main/.env.example
cp .env.example .env
```

`.env` 파일을 열어 아래 항목을 채웁니다.

```env
OUTLINE_BASE_URL=https://your-outline.example.com
OUTLINE_API_KEY=ol_api_xxxxxxxxxxxx
OUTLINE_WEBHOOK_SECRET=your_webhook_secret
QDRANT_API_KEY=strong_secret_key
```

### 2. 실행

```bash
curl -O https://raw.githubusercontent.com/sjw0066/outlineMcp/main/docker-compose.yml
docker compose up -d
```

| 서비스 | 호스트 포트 | 비고 |
|--------|-----------|------|
| Indexer (webhook) | `17000` | `http://localhost:17000` |
| MCP Server | `17080` | `http://localhost:17080/sse` |
| Qdrant | 미노출 | `rag-net` 내부에서만 접근 |
| Redis | 미노출 | `rag-net` 내부에서만 접근 |
| Worker / Beat | 없음 | 백그라운드 프로세스, 포트 노출 없음 |

> 최초 실행 시 Qdrant가 비어 있으면 Outline 전체 문서를 자동으로 인덱싱합니다.

---

## Outline Webhook 등록

Outline → **Settings → Webhooks → New webhook**

| 항목 | 값 |
|------|-----|
| URL | `http://<your-server-ip>:17000/webhook/outline` |
| Secret | `.env`의 `OUTLINE_WEBHOOK_SECRET` 값 |
| Events | `documents.create`, `documents.update`, `documents.delete` |

---

## MCP 클라이언트 연결

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "knowledge-base": {
      "url": "http://localhost:17080/sse"
    }
  }
}
```

### 제공 도구

#### `search_knowledge`

```
query  : str  — 자연어 검색어
limit  : int  — 반환 결과 수 (기본 5, 최대 20)
```

응답 예시:

```json
[
  {
    "title": "SSL 인증서 갱신 가이드",
    "url": "https://your-outline.example.com/doc/...",
    "snippet": "...",
    "score": 0.92,
    "tags": ["infra", "ssl"],
    "source": "outline",
    "collection": "project",
    "doc_id": "4de53efa-a7fa-4ec2-bb5a-b8b300bfe02d"
  }
]
```

---

## 임베딩 프로바이더 설정

`.env`에서 `EMBEDDING_PROVIDER`를 변경합니다.

| 프로바이더 | 설정 | 벡터 차원 | 특징 |
|-----------|------|----------|------|
| `gemini` (기본) | `GOOGLE_API_KEYS` | 3072 (조정 가능) | API 호출, 다중 키 라운드로빈 |
| `openai` | `OPENAI_API_KEY` | 1536 | API 호출, 고품질 |

> **주의**: 프로바이더나 `GEMINI_EMBEDDING_DIM`을 변경하면 임베딩 차원이 달라져 기존 데이터와 호환되지 않습니다. `qdrant_data` 볼륨을 삭제하고 재시작하세요.

### Gemini API 키 풀

`GOOGLE_API_KEYS`에 쉼표로 여러 키를 넣으면 요청마다 라운드로빈으로 순환하며, 429(rate limit)나 일시적 오류가 발생하면 자동으로 다음 키로 재시도합니다. 모든 키가 실패해야 해당 요청이 실패로 처리됩니다.

```env
GOOGLE_API_KEYS=key1,key2,key3,key4
```

---

## 수동 재동기화

기본은 증분 동기화(마지막 동기화 이후 변경된 문서만 재인덱싱, 삭제된 문서는 자동 제거):

```bash
curl -X POST http://localhost:17000/sync/outline

# 모든 문서를 강제로 전체 재인덱싱
curl -X POST "http://localhost:17000/sync/outline?full=true"

# 진행 상태 확인
curl http://localhost:17000/sync/status
```

---

## 개발 환경

소스에서 직접 빌드해서 실행:

```bash
git clone https://github.com/sjw0066/outlineMcp.git
cd outlineMcp
cp .env.example .env

docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

---

## 테스트

Qdrant/Outline/Redis 없이 순수 단위 테스트만 실행합니다 (외부 서비스는 모두 모킹):

```bash
pip install -r indexer/requirements.txt -r requirements-test.txt
python -m pytest tests/ -v
```

---

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EMBEDDING_PROVIDER` | `gemini` | `gemini` \| `openai` |
| `GOOGLE_API_KEYS` | — | Gemini 사용 시 필수, 쉼표로 구분된 키 풀 (`GEMINI_API_KEY`로 단일 키도 가능) |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | |
| `GEMINI_EMBEDDING_DIM` | `3072` | 출력 벡터 차원 (`output_dimensionality`) |
| `OPENAI_API_KEY` | — | OpenAI 사용 시 필수 |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant 주소 |
| `QDRANT_API_KEY` | — | 인증 키 (비워두면 인증 없음) |
| `OUTLINE_BASE_URL` | — | Outline 인스턴스 URL |
| `OUTLINE_API_KEY` | — | Outline API 키 |
| `OUTLINE_WEBHOOK_SECRET` | — | Webhook 서명 검증용 시크릿 |
| `SYNC_STATE_PATH` | `/data/sync_state.json` | 증분 동기화 커서 저장 경로 (Worker 컨테이너) |
| `REDIS_URL` | `redis://redis:6379/1` | Celery 브로커 겸 락 저장소 |
| `SYNC_INTERVAL_SECONDS` | `3600` | Beat가 증분 동기화를 큐잉하는 주기(초) |
| `MCP_TRANSPORT` | `sse` | `sse` \| `stdio` |
| `MCP_PORT` | `8080` | MCP 서버 포트 |

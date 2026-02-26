# outline-rag-mcp

Outline 문서를 자동으로 벡터화하여 AI 에이전트가 의미 기반 검색을 할 수 있게 해주는 RAG + MCP 서버입니다. Docker Compose 한 줄로 실행됩니다.

## 아키텍처

```
Outline (webhook)
      ↓
  Indexer        ← 문서 수신 → 청킹 → 임베딩 → Qdrant 저장
      ↓
  Qdrant         ← 벡터 DB
      ↓
  MCP Server     ← AI 에이전트가 search_knowledge() 호출
```

- **Indexer**: Outline webhook을 수신해 문서를 청킹·임베딩 후 Qdrant에 저장. 최초 실행 시 기존 문서 전체 자동 동기화.
- **MCP Server**: `search_knowledge` 도구를 SSE 방식으로 제공. Claude Desktop 등 MCP 클라이언트에서 연결.
- **임베딩**: HuggingFace 로컬 / OpenAI / Gemini 중 선택.

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
    "source": "outline"
  }
]
```

---

## 임베딩 프로바이더 설정

`.env`에서 `EMBEDDING_PROVIDER`를 변경합니다.

| 프로바이더 | 설정 | 벡터 차원 | 특징 |
|-----------|------|----------|------|
| `huggingface` (기본) | `HF_MODEL`, `HF_DEVICE` | 768 | 로컬 실행, 무료 |
| `openai` | `OPENAI_API_KEY` | 1536 | API 호출, 고품질 |
| `gemini` | `GEMINI_API_KEY` | 768 | API 호출 |

> **주의**: 프로바이더를 변경하면 임베딩 차원이 달라져 기존 데이터와 호환되지 않습니다. `qdrant_data` 볼륨을 삭제하고 재시작하세요.

### HuggingFace 모델 선택 가이드

| 모델 | 특징 |
|------|------|
| `intfloat/multilingual-e5-base` (기본) | 한국어 포함 다국어, CPU 적합 |
| `BAAI/bge-small-en-v1.5` | 영어 전용, 빠름 |
| `BAAI/bge-m3` | 고정밀, GPU 권장 |

---

## 수동 재동기화

모든 Outline 문서를 강제로 재인덱싱:

```bash
curl -X POST http://localhost:17000/sync/outline

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

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `EMBEDDING_PROVIDER` | `huggingface` | `huggingface` \| `openai` \| `gemini` |
| `HF_MODEL` | `intfloat/multilingual-e5-base` | HuggingFace 모델명 |
| `HF_DEVICE` | `cpu` | `cpu` \| `cuda` |
| `OPENAI_API_KEY` | — | OpenAI 사용 시 필수 |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `GEMINI_API_KEY` | — | Gemini 사용 시 필수 |
| `GEMINI_EMBEDDING_MODEL` | `models/text-embedding-004` | |
| `QDRANT_URL` | `http://qdrant:6333` | Qdrant 주소 |
| `QDRANT_API_KEY` | — | 인증 키 (비워두면 인증 없음) |
| `OUTLINE_BASE_URL` | — | Outline 인스턴스 URL |
| `OUTLINE_API_KEY` | — | Outline API 키 |
| `OUTLINE_WEBHOOK_SECRET` | — | Webhook 서명 검증용 시크릿 |
| `MCP_TRANSPORT` | `sse` | `sse` \| `stdio` |
| `MCP_PORT` | `8080` | MCP 서버 포트 |

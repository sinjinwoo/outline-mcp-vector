좋다 👍
지금 목표를 기준으로 **실제 구현 전에 바로 개발 들어갈 수 있는 설계 문서** 형태로 정리해 줄게.

👉 전제

* 임베딩: **Hugging Face 모델 로컬 실행**
* 소스: Outline / Notion / Obsidian
* 이벤트 기반 + 폴링 혼합
* Vector DB: Qdrant
* MCP 검색 제공
* Docker Compose 배포
* 오픈소스 공개 가능 구조

---

# 📘 MCP RAG Sync 설계 문서

## 1. 프로젝트 개요

### 목적

Outline, Notion, Obsidian 문서를 자동으로 수집하고 벡터화하여 AI 및 MCP 기반 검색에 활용 가능한 지식 인프라를 구축한다.

### 주요 기능

* 다중 문서 소스 연동
* 이벤트 기반 실시간 인덱싱
* 로컬 임베딩 생성 (HuggingFace)
* 벡터 검색 (Qdrant)
* MCP 검색 인터페이스 제공
* Docker Compose 배포 지원

---

## 2. 전체 아키텍처

```text
Sources
 ├─ Outline (webhook)
 ├─ Notion (poll)
 └─ Obsidian (filesystem scan)

        ↓

Connector Layer
        ↓
Document Normalizer
        ↓
Chunk Processor
        ↓
Embedding Engine (HF local)
        ↓
Vector Store (Qdrant)
        ↓
Search Layer
        ↓
MCP Server / AI Agent
```

---

## 3. 컴포넌트 설계

## 3.1 Connector Layer

### 역할

각 플랫폼에서 문서를 가져와 공통 포맷으로 변환.

### 인터페이스

```python
class Connector:
    def list_changed(self, since: datetime) -> list[str]:
        pass

    def get_document(self, doc_id: str) -> Document:
        pass

    def delete_document(self, doc_id: str):
        pass
```

---

### 3.1.1 Outline Connector

#### 방식

* Webhook 이벤트 수신
* Outline API로 문서 재조회

#### 이벤트 처리

| 이벤트    | 처리    |
| ------ | ----- |
| create | 인덱싱   |
| update | 재인덱싱  |
| delete | 벡터 삭제 |

---

### 3.1.2 Notion Connector

#### 방식

* Polling 기반
* last_edited_time 비교

#### 지원 대상

* Database
* Page 트리

---

### 3.1.3 Obsidian Connector

#### 방식

* Vault 디렉토리 스캔
* 수정시간 기준 증분 처리

#### 특징

* markdown parsing
* frontmatter tag 추출

---

## 3.2 Document Normalizer

### 역할

서로 다른 소스를 공통 스키마로 변환.

### 표준 문서 구조

```json
{
  "source": "outline",
  "doc_id": "abc123",
  "title": "SSL Renewal Guide",
  "text": "...",
  "url": "https://...",
  "tags": ["infra", "ssl"],
  "collection": "operations",
  "updated_at": "2026-02-26T10:00:00Z"
}
```

---

## 3.3 Chunk Processor

### 목표

검색 정확도와 속도를 최적화.

### 전략

#### Markdown 기반 분할 (기본)

* H1/H2 단위 분리
* 코드 블록 분리

#### 토큰 기반 분할 (fallback)

### 기본 설정

```
CHUNK_SIZE = 800 tokens
CHUNK_OVERLAP = 120 tokens
```

---

## 3.4 Embedding Engine (HuggingFace Local)

### 목적

문서를 벡터화하여 의미 기반 검색 가능하게 함.

### 지원 모델 (기본 추천)

| 모델                   | 특징             |
| -------------------- | -------------- |
| multilingual-e5-base | CPU 성능 우수, 다국어 |
| bge-small            | 빠름             |
| bge-m3               | 고정밀 (GPU 추천)   |

### 인터페이스

```python
class Embedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        pass
```

### 동작 방식

1. 최초 실행 시 HF Hub에서 모델 다운로드
2. 로컬 캐시 저장
3. CPU 기반 추론 수행

### 최적화

* batch inference
* int8 / ONNX 변환 (옵션)
* multi-thread 실행

---

## 3.5 Vector Store (Qdrant)

### 컬렉션 구조

* vector size: 모델에 따라 동적
* distance: cosine

### payload 구조

```json
{
  "doc_id": "...",
  "title": "...",
  "source": "outline",
  "url": "...",
  "tags": ["infra"],
  "updated_at": "..."
}
```

---

## 3.6 Indexing Strategy

### 이벤트 기반 (Outline)

Webhook → 재인덱싱

### 폴링 기반 (Notion/Obsidian)

주기적 변경 감지

### 증분 처리

* updated_at 비교
* 기존 벡터 삭제 후 재삽입

---

## 3.7 Search Layer

### 검색 흐름

1. 쿼리 임베딩 생성
2. Qdrant vector search
3. 결과 rerank (옵션)
4. 출처 포함 반환

---

## 3.8 MCP Server

### 제공 Tool

#### 문서 검색

```python
search_knowledge(query: str)
```

응답:

```json
[
  {
    "title": "SSL Renewal Guide",
    "url": "...",
    "snippet": "...",
    "score": 0.92
  }
]
```

---

## 4. 환경 변수 설계

### Core

```env
SOURCE_PROVIDER=outline
EMBEDDING_PROVIDER=huggingface
HF_MODEL=intfloat/multilingual-e5-base
HF_DEVICE=cpu

QDRANT_URL=http://qdrant:6333
```

### Outline

```env
OUTLINE_BASE_URL=
OUTLINE_API_KEY=
```

### Notion

```env
NOTION_API_TOKEN=
```

### Obsidian

```env
OBSIDIAN_VAULT_PATH=/vault
```

---

## 5. Docker Compose 배포 구조

```yaml
services:
  qdrant:
    image: qdrant/qdrant

  indexer:
    image: yourname/rag-indexer
    env_file: .env
    volumes:
      - ./vault:/vault

  mcp:
    image: yourname/rag-mcp
```

---

## 6. 성능 고려사항

### CPU 환경 최적화

* batch embedding
* chunk size 최적화
* polling 간격 조정

### 메모리 관리

* lazy loading
* streaming 처리

---

## 7. 보안 고려사항

* webhook secret 검증
* API 키 환경 변수 관리

---

## 8. 향후 확장 로드맵

### 검색 품질

* Hybrid search (BM25 + vector)
* reranker 추가

---

## 9. 사용 시나리오

### DevOps 운영 문서 검색

→ 장애 대응 속도 향상

### 팀 지식 검색

→ 문서 탐색 시간 절감

### AI Agent 지식 기반

→ 자동 문제 해결 지원

---

## 10. 설계 핵심 철학

✔ 소스 독립성
✔ 이벤트 기반 동기화
✔ 로컬 데이터 보호
✔ 확장 가능한 구조
✔ AI 에이전트 친화적 설계

## 최소 기능

1. outline 기반 RAG MCP서버 구성
2. 도커 이미지 배포.

## 추후 개발

1. notion 개발
2. obsidain 개발
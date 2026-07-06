지금까지 정리된 모든 내용(홈서버 HTTPS 기본 배포 환경, 17080 포트 분리 구조, Nginx 프록시 버퍼링 설정, Outline 사전 발급 순서 등)을 완벽하게 반영하여 그대로 복사 붙여넣기(Ctrl+C, Ctrl+V)해서 사용할 수 있는 최종 리드미(README.md)를 완성했습니다.

---

# 🚀 Outline RAG MCP Server

Outline 위키의 문서들을 자동으로 벡터화하여, AI 에이전트(Claude Desktop 등)가 **의미 기반 자연어 검색**을 할 수 있도록 지원하는 RAG(Retrieval-Augmented Generation) + MCP(Model Context Protocol) 서버입니다.

셀프 호스팅 중인 Outline Stack의 **기존 Redis를 공유하여 사용**하므로, 리소스를 낭비하지 않고 **Docker Compose 파일 하나로 즉시 연동**됩니다.

---

## ✨ 핵심 특징

* **단일 컨테이너 배포 (`outline-mcp-vector`)**: FastAPI, Celery (Worker/Beat), MCP 서버가 컨테이너 하나로 구동되어 관리가 편리합니다.
* **인프라 자원 최적화**: Outline이 이미 사용 중인 Redis 컨테이너를 함께 공유하되, 논리 디비(`db/1`)를 분리하여 격리된 큐를 구성합니다.
* **지능형 증분 동기화**: 실시간 웹훅(Webhook)과 주기적(기본 1시간) 스케줄러가 협업하여 `updated_at` 기준 변경·삭제된 문서만 스마트하게 추적 반영합니다.
* **Gemini Key Pool**: 여러 개의 Gemini API 키를 등록하면 라운드 로빈 방식으로 호출하며, Rate Limit(429) 발생 시 자동 Failover를 수행합니다.
* **MCP 토큰 인증**: MCP 서버(SSE)는 `MCP_AUTH_TOKENS`에 등록된 토큰이 없으면 아예 기동되지 않으며, 등록되지 않은 토큰으로의 요청은 모두 401로 거부됩니다. URL만 알아도 아무나 지식베이스를 검색할 수 없도록 막는 장치입니다.

---

## 🛠 아키텍처

```text
Outline Stack (기존 인프라)            outline-net (공유 네트워크)
┌──────────────────────────────┐              │
│  [Outline]     [Redis]       │◄─────────────┼──────────────┐
└───────────────────▲──────────┘              │              │
                    │ (논리 DB /1 재사용)       │               │
┌───────────────────┴─────────────────────────▼──────────────┼
│ outline-mcp-vector (1 Container Stack)                     │              
│  - FastAPI (웹훅 수신 및 즉시 응답)                           │
│  - Celery Worker & Beat (백그라운드 청킹 / 임베딩 / 스케줄링)   │
│  - MCP Server (search_knowledge 도구 제공 via SSE)           │
└─────────────────────────────────────────────┬──────────────┘
                                              │ (rag-net)
                                      ┌──────▼───────────────────────┐
                                      │ Qdrant (내부 벡터 DB)         │
                                      └──────────────────────────────┘

```

---

## 📦 3분 빠른 시작 (Quick Start)

### 1. Outline 사전 준비 및 Webhook 생성

RAG 서버를 띄우기 전에 Outline 관리자 화면에서 연동에 필요한 정보들을 먼저 확보해야 합니다.

1. **API Key 발급**: Outline **Settings → API Tokens**에서 새 토큰을 생성합니다 (`OUTLINE_API_KEY`).
2. **Webhook 등록 및 Secret 생성**: Outline **Settings → Webhooks → New webhook**으로 이동하여 아래와 같이 등록하고 **Secret** 값을 복사해둡니다.
* **URL**: `http://<your-server-ip>:17000/webhook/outline` (RAG 서버가 수신할 주소)
* **Events**: `documents.create`, `documents.update`, `documents.delete` 선택
* *생성 후 화면에 표시되는 복잡한 문자열 시크릿을 잘 보관하세요 (`OUTLINE_WEBHOOK_SECRET`).*



### 2. Outline Docker Network 확인

RAG 서버가 Outline 및 Redis 컨테이너와 내부망으로 통신할 수 있도록, 기존 Outline의 `docker-compose.yml`에 아래와 같이 명시적인 외부망 이름(`outline-net`)이 지정되어 있어야 합니다.

```yaml
# 기존 Outline docker-compose.yml 예시 (없다면 추가 후 Outline 재시작 필요)
services:
  outline:
    networks: [outline-net]
  redis:
    networks: [outline-net]

networks:
  outline-net:
    name: outline-net

```

### 3. 환경 변수 (`.env`) 설정

설치할 디렉토리에 `.env` 파일을 생성하고 1번 단계에서 확보한 값들과 필수 설정들을 입력합니다.

```env
# Outline 연동 설정 (1번 단계에서 가져온 값 입력)
OUTLINE_API_KEY=ol_api_xxxxxxxxxxxx        # Outline API 토큰
OUTLINE_WEBHOOK_SECRET=your_secret_key     # Outline 웹훅 화면에서 복사한 Secret
OUTLINE_API_URL=http://outline:3000        # 내부 도커망 통신용 URL
OUTLINE_PUBLIC_URL=https://wiki.domain.com # 실제 사용자가 브라우저로 접속하는 공개 URL

# AI 및 벡터 DB 설정
GOOGLE_API_KEYS=key1,key2,key3             # Gemini API 키 (쉼표로 여러 개 등록 가능)
QDRANT__SERVICE__API_KEY=strong_qdrant_key # Qdrant 인증용 임의의 비밀번호

# MCP 인증 — 여기 등록된 토큰이 없으면 MCP 서버가 아예 기동되지 않습니다.
# 쉼표로 여러 개 등록해 클라이언트별로 다른 토큰을 발급하세요. 생성 예: openssl rand -hex 32
MCP_AUTH_TOKENS=token_for_me,token_for_teammate

```

### 4. Docker Compose 실행

아래 내용으로 `docker-compose.yml` 파일을 만들고 곧바로 실행합니다.

```yaml
version: '3.8'

services:
  qdrant:
    image: qdrant/qdrant:latest
    expose:
      - "6333"
    environment:
      - QDRANT__SERVICE__API_KEY
    volumes:
      - qdrant_data:/qdrant/storage
    networks:
      - rag-net
    healthcheck:
      test: ["CMD-SHELL", "bash -c 'echo -e \"GET /healthz HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n\" > /dev/tcp/localhost/6333' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5

  outline-mcp-vector:
    image: sjw0066/outline-mcp-vector:latest
    env_file: .env
    environment:
      - QDRANT_URL=http://qdrant:6333
      - REDIS_URL=redis://redis:6379/1 # Outline의 Redis 컨테이너를 함께 공유 (DB 1번 사용)
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8080
    ports:
      - "17000:8000"   # FastAPI 포트 (Webhook, Sync 수신)
      - "17080:8080"   # FastMCP SSE 포트
    volumes:
      - sync_state:/data
    networks:
      - rag-net
      - outline-net    # Outline의 기존 대역에 합류
    depends_on:
      qdrant:
        condition: service_healthy

networks:
  rag-net:
    driver: bridge
  outline-net:
    external: true     # 이미 구동 중인 Outline 네트워크를 참조

volumes:
  qdrant_data:
  sync_state:

```

```bash
docker compose up -d

```

> **💡 참고**: 컨테이너가 정상적으로 실행되면, Qdrant 벡터 DB가 비어있는 것을 감지하고 Outline의 기존 전체 문서를 자동으로 긁어와 초기 인덱싱(전체 동기화)을 수행합니다.

---

## 🔗 외부 클라이언트 연동 가이드 (HTTPS / SSE 방식)

홈서버 외부에 있는 PC에서 Nginx 등의 역방향 프록시(Reverse Proxy) 및 SSL 인증서가 적용된 홈서버 도메인을 통해 안전하게 연동하는 방법입니다.

### 1. Nginx 역방향 프록시 설정 (필수)

FastMCP가 사용하는 SSE(Server-Sent Events) 스트리밍이 끊기지 않도록, 도메인 서브블록(`proxy_pass`) 설정을 반드시 RAG 서버의 **`17080` 포트**로 매핑하고 **프록시 버퍼링 비활성화** 옵션을 추가해 주세요.

```nginx
server {
    listen 443 ssl;
    server_name mcp.your-domain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        # ⭐ 핵심: 도커 콤포즈가 열어놓은 MCP 포트(17080)로 전달합니다.
        proxy_pass http://localhost:17080;
        
        # SSE 연결 실시간 유지를 위한 필수 설정
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        chunked_transfer_encoding off;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

```

### 2. Claude Desktop 연동

MCP 서버는 `.env`의 `MCP_AUTH_TOKENS`에 등록된 토큰이 없으면 아무 요청도 처리하지 않습니다(401). 외부 PC의
`claude_desktop_config.json`에 프록시 세팅이 완료된 홈서버의 HTTPS 도메인 주소와 패스(`/sse`)를 기입하되,
URL 쿼리 파라미터로 토큰을 함께 넘겨주세요 (`?token=...`). Claude Desktop의 `url` 필드는 커스텀 헤더를
지정할 수 없기 때문에, `Authorization: Bearer` 헤더 대신 쿼리 파라미터로도 인증되도록 만들어 두었습니다.

```json
{
  "mcpServers": {
    "outline-knowledge-base": {
      "url": "https://mcp.your-domain.com/sse?token=token_for_teammate"
    }
  }
}

```

커스텀 헤더를 지정할 수 있는 클라이언트(예: `mcp-remote`)라면 `Authorization: Bearer token_for_teammate`
헤더로도 동일하게 인증할 수 있습니다.

---

## ⚙️ 수동 동기화 및 관리 API

백그라운드 자동 동기화(1시간 주기) 외에 직접 인덱싱을 제어하고 싶을 때 사용합니다.

* **증분 동기화 트리거**: `POST http://localhost:17000/sync/outline`
* **전체 강제 재인덱싱**: `POST http://localhost:17000/sync/outline?full=true`
* **동기화 상태 확인**: `GET http://localhost:17000/sync/status`

---

이 오픈소스 프로젝트가 도움이 되셨다면 ⭐️ **Star**로 응원해 주세요! 문의 사항은 언제든 Issue 탭에 남겨주시기 바랍니다.
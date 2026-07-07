🌐 **Language**: [English](README.md) | **한국어**

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
* **MCP 인증은 옵션(Keycloak/OAuth)**: MCP 서버(Streamable HTTP)는 기본이 완전 개방입니다 — `MCP_OAUTH_ENABLED=true`(+ issuer/resource/audience)로 켜면 Keycloak이 발급한 Bearer JWT를 요구하도록 전환됩니다. 이 프로젝트는 자체 인가 서버를 운영하지 않고 리소스 서버 역할만 합니다.

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
│  - MCP Server (search_knowledge 도구 제공 via Streamable HTTP) │
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

# DNS rebinding 방지(Host/Origin 헤더 검증). 배포 도메인을 쉼표로 나열하세요. 비워두면
# 꺼진 채로 동작합니다(기존과 동일). 악성 웹페이지가 브라우저를 통해 공격하는 경우에만
# 관련 있음 — curl, Claude Desktop 원격 커넥터 같은 비-브라우저 클라이언트는 Origin
# 헤더를 안 보내므로 이 설정과 무관하게 항상 통과합니다.
# MCP_ALLOWED_HOSTS=mcp.your-domain.com
# MCP_ALLOWED_ORIGINS=https://mcp.your-domain.com

# MCP 인증 — 기본은 완전 개방(인증 없음)입니다. Keycloak이 발급한 Bearer JWT를 요구하려면
# MCP_OAUTH_ENABLED=true로 두고 나머지 값을 채우세요. 이 서버는 리소스 서버 역할만 하며
# Keycloak을 직접 띄우지 않습니다(테스트용 참고 구성은 docs/keycloak-reference-compose.yml).
# MCP_OAUTH_ENABLED=true
# MCP_OAUTH_ISSUER_URL=https://keycloak.your-domain.com/realms/myrealm
# MCP_OAUTH_RESOURCE_URL=https://mcp.your-domain.com/mcp
# MCP_OAUTH_AUDIENCE=outline-mcp-client

```

`MCP_OAUTH_*` 값들이 다루는 OAuth 개념(리소스 서버 vs 인가 서버, Protected Resource Metadata 등)이 낯설다면 MCP 공식 스펙의 [Authorization 가이드](https://modelcontextprotocol.io/docs/tutorials/security/authorization)를 참고하세요.

| 변수 | 필수 여부 | 기본값 | 설명 |
|---|---|---|---|
| `OUTLINE_API_KEY` | **필수** | — | Outline API 토큰 (Settings → API & Apps) |
| `OUTLINE_WEBHOOK_SECRET` | **필수** | — | Outline 웹훅 서명 검증용 secret. 비워두면 서명 검증을 아예 건너뜁니다 — 권장하지 않음 |
| `OUTLINE_BASE_URL` | **필수** | — | Outline 공개 URL. 아래 `OUTLINE_API_URL`/`OUTLINE_PUBLIC_URL`의 기본값이기도 함 |
| `GOOGLE_API_KEYS` | **필수**¹ | — | 쉼표로 구분한 Gemini API 키 풀. 라운드로빈 + 실패 시 자동 전환 |
| `QDRANT__SERVICE__API_KEY` | **필수** | — | 임의의 문자열. Qdrant 컨테이너 자체의 `service.api_key`로도 그대로 전달됨 |
| `GEMINI_API_KEY` | 선택 | — | ¹ `GOOGLE_API_KEYS` 풀 대신 키 하나만 쓸 때의 대체 값 |
| `OUTLINE_API_URL` | 선택 | = `OUTLINE_BASE_URL` | 실제 Outline API 호출용 내부 도커망 URL |
| `OUTLINE_PUBLIC_URL` | 선택 | = `OUTLINE_BASE_URL` | 검색 결과 문서 링크에 쓰이는 공개 URL |
| `GEMINI_EMBEDDING_DIM` | 선택 | `3072` | 출력 벡터 차원 |
| `GEMINI_TIMEOUT_MS` | 선택 | `30000` | Gemini 호출 타임아웃(밀리초) |
| `QDRANT_URL` | 선택 | `http://localhost:6333` | |
| `QDRANT_TIMEOUT_SECONDS` | 선택 | `10` | Qdrant 호출 타임아웃(초) |
| `MCP_HOST` | 선택 | `0.0.0.0` | |
| `MCP_PORT` | 선택 | `8080` | |
| `MCP_ALLOWED_HOSTS` | 선택 | *(비어있으면 검증 꺼짐)* | DNS rebinding 방지용 Host 허용 목록 (쉼표 구분) |
| `MCP_ALLOWED_ORIGINS` | 선택 | *(비어있음)* | 같은 보호 기능의 Origin 허용 목록 |
| `MCP_OAUTH_ENABLED` | 선택 | `false` | Keycloak/OAuth Bearer 토큰 인증 켜기. `false`면 서버가 완전 개방 상태 |
| `MCP_OAUTH_ISSUER_URL` | `MCP_OAUTH_ENABLED=true`면 필수 | — | Keycloak realm의 issuer URL |
| `MCP_OAUTH_RESOURCE_URL` | `MCP_OAUTH_ENABLED=true`면 필수 | — | 이 서버 자신의 외부 접근 URL (`/mcp` 경로 포함) |
| `MCP_OAUTH_AUDIENCE` | `MCP_OAUTH_ENABLED=true`면 필수 | — | Keycloak 클라이언트가 발급하는 토큰의 `aud` 클레임과 일치해야 함 |
| `MCP_OAUTH_JWKS_URL` | 선택 | `{issuer}/protocol/openid-connect/certs` | Keycloak 기본 경로가 아닌 IdP를 쓸 때만 필요 |
| `REDIS_URL` | 선택 | `redis://redis:6379/1` | Outline의 Redis 컨테이너를 논리 DB 1번으로 공유 |
| `SYNC_INTERVAL_SECONDS` | 선택 | `3600` | Celery Beat의 증분 동기화 주기 |

**`MCP_OAUTH_*`용 Keycloak client 설정하기:** `MCP_OAUTH_ISSUER_URL`이 가리키는 realm의 Keycloak 관리자 콘솔에서 —

1. 새로 만든 realm에는 사용자가 하나도 없습니다 — realm 생성 마법사가 따로 물어보지도 않고, 콘솔에 로그인할 때 쓴 `master` realm의 admin 계정도 그대로 넘어오지 않습니다. 이 realm에 브라우저로 직접 로그인할 일이 있다면(순수 `client_credentials` 머신 간 통신이 아니라 Claude의 브라우저 기반 OAuth 로그인 같은 경우) 먼저 실제 사용자를 만드세요: **Users** → **Create new user**, 생성 후 해당 사용자의 **Credentials** 탭 → **Set password**.
2. **Clients** → **Create client** → client ID를 정합니다 (이 값을 그대로 `MCP_OAUTH_AUDIENCE`에 씁니다). 이 클라이언트로 브라우저 로그인 플로우를 탈 거라면, Settings 탭에서 **Standard flow**도 켜고, **Valid redirect URIs**에 호출하는 쪽(예: 클로드는 `https://claude.ai/api/mcp/auth_callback`)의 콜백 주소를 등록하세요 — 이걸 빼먹으면 로그인 자체는 성공하고 나서 마지막에 리다이렉트가 거부되는데, 겉보기엔 로그인 실패처럼 보여서 헷갈리기 쉽습니다.
3. 생성한 클라이언트 클릭 → **Client scopes** 탭 → `<client-id>-dedicated` 스코프 클릭.
4. **Add mapper** → **By configuration** → **Audience** 선택 후, **Included Client Audience**에 방금 그 클라이언트를 지정합니다 — 이게 실제로 `aud` 클레임을 채워주는 부분이라, 이걸 안 하면 발급되는 토큰의 `aud`가 Keycloak 기본값(`account`)으로만 찍혀서 매 요청이 401로 막힙니다.
5. 저장한 뒤, 실제로 발급된 토큰을 (예: jwt.io에서) 디코드해서 `aud`에 해당 client ID가 정말 들어갔는지 확인하세요 — audience 필드를 비운 채 저장하면 아무 값도 안 들어가는데, 저장 자체는 성공한 것처럼 보여서 놓치기 쉽습니다.

`MCP_OAUTH_RESOURCE_URL` 관련해서 하나 더: 이 값은 실제로 **클라이언트가 도달 가능한 주소**여야 합니다 — 클라이언트가 클로드라면, 그건 사용자분 컴퓨터가 아니라 Anthropic 클라우드에서 실행됩니다. 여기에 `localhost`를 넣으면 401 응답의 `WWW-Authenticate` 헤더가 도달 불가능한 메타데이터 주소를 광고하는 셈이라 OAuth 디스커버리가 조용히 실패합니다. 로컬 개발 서버를 ngrok 같은 걸로 터널링하고 있다면 그 터널의 실제 공개 HTTPS 주소를 넣어야 하고, 무료 터널은 재시작할 때마다 주소가 바뀐다는 점도 기억하세요. 그리고 docker-compose로 띄운 상태라면 `docker compose restart`는 기존 컨테이너의 예전 환경을 그대로 재사용할 뿐 `.env`를 다시 읽지 않으니, `docker compose up -d`로 재생성해야 합니다.

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
      - "17080:8080"   # FastMCP Streamable HTTP 포트
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

## 🔗 외부 클라이언트 연동 가이드 (HTTPS / Streamable HTTP 방식)

홈서버 외부에 있는 PC에서 Nginx 등의 역방향 프록시(Reverse Proxy) 및 SSL 인증서가 적용된 홈서버 도메인을 통해 안전하게 연동하는 방법입니다.

### 1. Nginx 역방향 프록시 설정 (필수)

FastMCP의 Streamable HTTP transport가 쓰는 스트리밍 응답이 끊기지 않도록, 도메인 서브블록(`proxy_pass`) 설정을 반드시 RAG 서버의 **`17080` 포트**로 매핑하고 **프록시 버퍼링 비활성화** 옵션을 추가해 주세요.

```nginx
server {
    listen 443 ssl;
    server_name mcp.your-domain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        # ⭐ 핵심: 도커 콤포즈가 열어놓은 MCP 포트(17080)로 전달합니다.
        proxy_pass http://localhost:17080;
        
        # 스트리밍 연결 실시간 유지를 위한 필수 설정
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

기본값(`MCP_OAUTH_ENABLED` 미설정)에서는 MCP 서버가 인증 검사 없이 모든 요청을 처리합니다 — VPN/사설망
안에서만 접근 가능하다면 문제없지만, 외부에 그대로 노출된다면 URL만 알아도 지식베이스를 검색할 수 있으니
리버스 프록시 IP 허용목록이나 VPN 등으로 앞단을 막아두세요. 이 모드에서는 그냥 URL만 등록하면 됩니다.

`MCP_OAUTH_ENABLED=true`(Keycloak)로 켜두면, 커넥터 추가 화면에서 **Advanced settings → OAuth Client ID / OAuth Client Secret**을 비워두지 말고 Keycloak client 자격증명을 직접 채워 넣으세요. Claude Desktop도 이론상 서버의 OAuth 메타데이터를 스스로 찾아 Dynamic Client Registration을 시도할 수 있지만, Keycloak의 기본 "Trusted Hosts" Client Registration Policy가 낯선 호스트에서의 익명 DCR을 거부하기 때문에, 실제로는 아래 Claude Code 설정과 마찬가지로 미리 등록해둔 client가 필요합니다 (그 client의 **Valid redirect URIs**에 클로드의 콜백 `https://claude.ai/api/mcp/auth_callback`도 등록해야 함). 자격증명까지 넣고 추가하면 최초 연결 시 평범한 브라우저 로그인/동의 화면으로 안내됩니다 — config에 붙여 넣을 정적 토큰이 따로 없습니다. 로컬에서 테스트해보고 싶다면 `docs/keycloak-reference-compose.yml`의 임시 Keycloak realm을 참고하세요. 브라우저 기반 OAuth 플로우를 탈 수 없는 클라이언트는 이미 발급받은 Keycloak 액세스 토큰을 `Authorization: Bearer <token>` 헤더로 직접 보내는 방식으로도 인증할 수 있습니다.

### 3. Claude Code(CLI) 연동

OAuth가 꺼져 있으면 다른 HTTP MCP 서버와 똑같이 등록하면 됩니다:

```bash
claude mcp add --transport http outline-knowledge-base https://mcp.your-domain.com/mcp
```

`MCP_OAUTH_ENABLED=true`인 경우엔 Dynamic Client Registration을 쓰지 말고 미리 등록해둔 client로 붙이세요 — Keycloak의 기본 "Trusted Hosts" Client Registration Policy가 낯선 호스트에서 오는 익명 DCR을 거부하는데(ngrok 같은 개발용 터널이 딱 여기 걸립니다), Claude Code 자체의 OAuth 콜백도 로컬 루프백 주소(`http://localhost:PORT/callback`)라서 Claude Desktop의 콜백(`https://claude.ai/api/mcp/auth_callback`)과 다릅니다 — 두 클라이언트를 같은 Keycloak client로 같이 쓰려면 둘 다 등록해야 합니다.

1. Keycloak에서 그 client의 **Valid redirect URIs**에 `http://localhost:8080/callback`(포트는 아래와 맞추면 임의로 선택 가능)을 다른 클라이언트의 콜백과 나란히 추가합니다.
2. 그 client의 자격증명으로 서버를 등록합니다:
   ```bash
   claude mcp add-json outline-knowledge-base \
     '{"type":"http","url":"https://mcp.your-domain.com/mcp","oauth":{"clientId":"your-client-id","callbackPort":8080}}' \
     --client-secret
   ```
   (시크릿 입력을 물어보고, `.mcp.json`이 아니라 시스템 키체인/credential store에 저장됩니다)
3. `claude mcp login outline-knowledge-base` (또는 세션 안에서 `/mcp`)로 브라우저 로그인을 진행합니다.

---

## ⚙️ 수동 동기화 및 관리 API

백그라운드 자동 동기화(1시간 주기) 외에 직접 인덱싱을 제어하고 싶을 때 사용합니다.

* **증분 동기화 트리거**: `POST http://localhost:17000/sync/outline`
* **전체 강제 재인덱싱**: `POST http://localhost:17000/sync/outline?full=true`
* **동기화 상태 확인**: `GET http://localhost:17000/sync/status`

---

이 오픈소스 프로젝트가 도움이 되셨다면 ⭐️ **Star**로 응원해 주세요! 문의 사항은 언제든 Issue 탭에 남겨주시기 바랍니다.

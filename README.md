[![License](https://img.shields.io/github/license/sinjinwoo/outlineMcp.svg)](LICENSE)
[![CI](https://github.com/sinjinwoo/outlineMcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sinjinwoo/outlineMcp/actions/workflows/ci.yml)
[![Build](https://github.com/sinjinwoo/outlineMcp/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/sinjinwoo/outlineMcp/actions/workflows/docker-publish.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/sjw0066/outline-mcp-vector.svg)](https://hub.docker.com/r/sjw0066/outline-mcp-vector)
[![Docker Image Version](https://img.shields.io/docker/v/sjw0066/outline-mcp-vector?sort=semver&label=version)](https://hub.docker.com/r/sjw0066/outline-mcp-vector/tags)

🌐 **Language**: **English** | [한국어](README.ko.md)

---

# 🚀 Outline RAG MCP Server

A RAG (Retrieval-Augmented Generation) + MCP (Model Context Protocol) server that automatically vectorizes the documents in your Outline wiki, so AI agents (Claude Desktop, etc.) can run **semantic natural-language search** over them.

It **shares your self-hosted Outline stack's existing Redis** instead of spinning up its own, so it drops in with **a single Docker Compose file** and no wasted resources.

---

## ✨ Key Features

* **Single-container deployment (`outline-mcp-vector`)**: FastAPI, Celery (Worker/Beat), and the MCP server all run in one container, which keeps operations simple.
* **Infra-efficient**: Reuses Outline's existing Redis container, isolated on its own logical DB (`db/1`) so the two queues never collide.
* **Smart incremental sync**: Real-time webhooks and a periodic scheduler (hourly by default) work together, tracking only documents changed or deleted since the last `updated_at` cursor — no full re-embed on every run.
* **Gemini key pool**: Register multiple Gemini API keys and they're called round-robin, with automatic failover when one hits a rate limit (429).
* **MCP token auth**: The MCP server (SSE) refuses to even start unless at least one token is registered in `MCP_AUTH_TOKENS`, and any request with an unregistered token gets a 401. This stops anyone who merely knows the URL from searching your knowledge base.

---

## 🛠 Architecture

```text
Outline Stack (existing infra)         outline-net (shared network)
┌──────────────────────────────┐              │
│  [Outline]     [Redis]       │◄─────────────┼──────────────┐
└───────────────────▲──────────┘              │              │
                    │ (reuses logical DB /1)   │               │
┌───────────────────┴─────────────────────────▼──────────────┼
│ outline-mcp-vector (1 Container Stack)                     │              
│  - FastAPI (receives webhooks, responds immediately)        │
│  - Celery Worker & Beat (background chunking/embedding/sched)│
│  - MCP Server (serves the search_knowledge tool over SSE)    │
└─────────────────────────────────────────────┬──────────────┘
                                              │ (rag-net)
                                      ┌──────▼───────────────────────┐
                                      │ Qdrant (internal vector DB)   │
                                      └──────────────────────────────┘

```

---

## 📦 3-Minute Quick Start

### 1. Prep Outline and create a webhook

Before starting the RAG server, gather what you need from the Outline admin screen.

1. **Issue an API key**: In Outline, go to **Settings → API Tokens** and create a new token (`OUTLINE_API_KEY`).
2. **Register a webhook and grab its secret**: Go to **Settings → Webhooks → New webhook**, register it as below, and copy the **Secret** shown.
* **URL**: `http://<your-server-ip>:17000/webhook/outline` (the address the RAG server listens on)
* **Events**: select `documents.create`, `documents.update`, `documents.delete`
* *Keep the secret string shown after creation somewhere safe (`OUTLINE_WEBHOOK_SECRET`).*



### 2. Check Outline's Docker network

For the RAG server to reach the Outline and Redis containers over the internal network, your existing Outline `docker-compose.yml` needs an explicit external network name (`outline-net`).

```yaml
# Add this to your existing Outline docker-compose.yml if it's missing, then restart Outline
services:
  outline:
    networks: [outline-net]
  redis:
    networks: [outline-net]

networks:
  outline-net:
    name: outline-net

```

### 3. Set environment variables (`.env`)

Create a `.env` file in your install directory and fill in the values from step 1 plus the required settings.

```env
# Outline integration (values gathered in step 1)
OUTLINE_API_KEY=ol_api_xxxxxxxxxxxx        # Outline API token
OUTLINE_WEBHOOK_SECRET=your_secret_key     # Secret copied from the Outline webhook screen
OUTLINE_API_URL=http://outline:3000        # Internal Docker-network URL
OUTLINE_PUBLIC_URL=https://wiki.domain.com # Public URL real users hit in their browser

# AI and vector DB settings
GOOGLE_API_KEYS=key1,key2,key3             # Gemini API keys (comma-separated for a round-robin pool)
QDRANT__SERVICE__API_KEY=strong_qdrant_key # Any string you like, used to authenticate to Qdrant

# MCP auth — the MCP server won't start at all without at least one token registered here.
# Comma-separate multiple tokens to issue one per client. Example: openssl rand -hex 32
MCP_AUTH_TOKENS=token_for_me,token_for_teammate

```

### 4. Run Docker Compose

Create a `docker-compose.yml` with the contents below and bring it up.

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
      - REDIS_URL=redis://redis:6379/1 # Shares Outline's Redis container (logical DB 1)
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8080
    ports:
      - "17000:8000"   # FastAPI port (webhook, sync)
      - "17080:8080"   # FastMCP SSE port
    volumes:
      - sync_state:/data
    networks:
      - rag-net
      - outline-net    # Joins Outline's existing network
    depends_on:
      qdrant:
        condition: service_healthy

networks:
  rag-net:
    driver: bridge
  outline-net:
    external: true     # References the already-running Outline network

volumes:
  qdrant_data:
  sync_state:

```

```bash
docker compose up -d

```

> **💡 Note**: Once the container is up, it detects that the Qdrant vector DB is empty and automatically pulls in all of Outline's existing documents for an initial full sync.

---

## 🔗 External Client Setup Guide (HTTPS / SSE)

How to connect securely from a PC outside your home server, through an Nginx reverse proxy and an SSL certificate on your home server's domain.

### 1. Nginx reverse proxy config (required)

So the SSE (Server-Sent Events) streaming FastMCP uses doesn't get cut off, make sure your domain's `proxy_pass` block maps to the RAG server's **port `17080`** and disables proxy buffering.

```nginx
server {
    listen 443 ssl;
    server_name mcp.your-domain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        # ⭐ Key part: forward to the MCP port (17080) exposed by docker-compose.
        proxy_pass http://localhost:17080;
        
        # Required to keep the SSE connection alive in real time
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

### 2. Claude Desktop setup

The MCP server won't process any request unless it carries a token registered in `.env`'s `MCP_AUTH_TOKENS` (returns 401 otherwise). In your external PC's `claude_desktop_config.json`, enter your proxied home server's HTTPS domain and path (`/sse`), passing the token as a URL query parameter (`?token=...`). Claude Desktop's `url` field can't attach custom headers, so the server accepts the token via query param as well as `Authorization: Bearer`.

```json
{
  "mcpServers": {
    "outline-knowledge-base": {
      "url": "https://mcp.your-domain.com/sse?token=token_for_teammate"
    }
  }
}

```

Clients that can set custom headers (e.g. `mcp-remote`) can authenticate the same way with an `Authorization: Bearer token_for_teammate` header.

---

## ⚙️ Manual Sync & Management API

For controlling indexing directly, beyond the background auto-sync (hourly by default).

* **Trigger an incremental sync**: `POST http://localhost:17000/sync/outline`
* **Force a full re-index**: `POST http://localhost:17000/sync/outline?full=true`
* **Check sync status**: `GET http://localhost:17000/sync/status`

---

If this open-source project has been useful to you, please support it with a ⭐️ **Star**! Feel free to leave any questions in the Issues tab.

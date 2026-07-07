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
* **MCP auth is opt-in Keycloak/OAuth**: the MCP server (Streamable HTTP) is fully open by default — set `MCP_OAUTH_ENABLED=true` (plus issuer/resource/audience) to require a Keycloak-issued Bearer JWT instead. This project only ever acts as an OAuth Resource Server; it doesn't run or provision Keycloak itself.

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
│  - MCP Server (serves the search_knowledge tool over Streamable HTTP) │
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

# DNS rebinding protection (Host/Origin header validation). Comma-separate your deployed
# domain(s); leave unset to keep this off (pre-existing behavior). Only relevant against
# malicious webpages attacking through a browser — non-browser clients (curl, Claude
# Desktop's remote connector) don't send an Origin header and aren't affected either way.
# MCP_ALLOWED_HOSTS=mcp.your-domain.com
# MCP_ALLOWED_ORIGINS=https://mcp.your-domain.com

# MCP auth — off by default (server is fully open). Set MCP_OAUTH_ENABLED=true and fill in
# the rest to require a Keycloak-issued Bearer JWT instead. This server is a Resource Server
# only; it validates against your Keycloak realm's JWKS, it doesn't run Keycloak itself
# (see docs/keycloak-reference-compose.yml for a throwaway realm to test against).
# MCP_OAUTH_ENABLED=true
# MCP_OAUTH_ISSUER_URL=https://keycloak.your-domain.com/realms/myrealm
# MCP_OAUTH_RESOURCE_URL=https://mcp.your-domain.com/mcp
# MCP_OAUTH_AUDIENCE=outline-mcp-client

```

See the MCP spec's own [Authorization guide](https://modelcontextprotocol.io/docs/tutorials/security/authorization) for the OAuth concepts behind the `MCP_OAUTH_*` vars above (Resource Server vs Authorization Server, Protected Resource Metadata, etc).

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `OUTLINE_API_KEY` | **Required** | — | Outline API token (Settings → API & Apps) |
| `OUTLINE_WEBHOOK_SECRET` | **Required** | — | Outline webhook signing secret. If left empty, webhook signature verification is skipped entirely — not recommended |
| `OUTLINE_BASE_URL` | **Required** | — | Outline's public URL; also the fallback for `OUTLINE_API_URL`/`OUTLINE_PUBLIC_URL` below |
| `GOOGLE_API_KEYS` | **Required**¹ | — | Comma-separated Gemini API key pool, round-robin with failover |
| `QDRANT__SERVICE__API_KEY` | **Required** | — | Any string; also passed to the Qdrant container itself as `service.api_key` |
| `GEMINI_API_KEY` | Optional | — | ¹ Single-key fallback if you don't need `GOOGLE_API_KEYS`' pool |
| `OUTLINE_API_URL` | Optional | = `OUTLINE_BASE_URL` | Internal Docker-network URL for actual Outline API calls |
| `OUTLINE_PUBLIC_URL` | Optional | = `OUTLINE_BASE_URL` | Public URL used to build the doc links shown in search results |
| `GEMINI_EMBEDDING_DIM` | Optional | `3072` | Output vector dimensionality |
| `GEMINI_TIMEOUT_MS` | Optional | `30000` | Per-request Gemini timeout, milliseconds |
| `QDRANT_URL` | Optional | `http://localhost:6333` | |
| `QDRANT_TIMEOUT_SECONDS` | Optional | `10` | Per-request Qdrant timeout, seconds |
| `MCP_HOST` | Optional | `0.0.0.0` | |
| `MCP_PORT` | Optional | `8080` | |
| `MCP_ALLOWED_HOSTS` | Optional | *(empty → protection off)* | Comma-separated Host allow-list for DNS rebinding protection |
| `MCP_ALLOWED_ORIGINS` | Optional | *(empty)* | Comma-separated Origin allow-list, same protection |
| `MCP_OAUTH_ENABLED` | Optional | `false` | Turns on Keycloak/OAuth Bearer-token auth; server is fully open while `false` |
| `MCP_OAUTH_ISSUER_URL` | Required if `MCP_OAUTH_ENABLED=true` | — | Keycloak realm issuer URL |
| `MCP_OAUTH_RESOURCE_URL` | Required if `MCP_OAUTH_ENABLED=true` | — | This server's own external URL, including the `/mcp` path |
| `MCP_OAUTH_AUDIENCE` | Required if `MCP_OAUTH_ENABLED=true` | — | Must match the `aud` claim your Keycloak client issues |
| `MCP_OAUTH_JWKS_URL` | Optional | `{issuer}/protocol/openid-connect/certs` | Only needed if your IdP doesn't use Keycloak's default path |
| `REDIS_URL` | Optional | `redis://redis:6379/1` | Shares Outline's Redis container, logical DB 1 |
| `SYNC_INTERVAL_SECONDS` | Optional | `3600` | Celery Beat's incremental-sync interval |

**Setting up the Keycloak client for `MCP_OAUTH_*`:** in the Keycloak admin console, on the realm you're pointing `MCP_OAUTH_ISSUER_URL` at —

1. A brand-new realm has no users of its own — the realm-creation wizard doesn't ask for one, and the `master` realm's admin account you log into the console with does **not** carry over. If anyone will ever log in interactively against this realm (e.g. Claude's browser-based OAuth flow, as opposed to a pure machine-to-machine `client_credentials` setup), create a real user first: **Users** → **Create new user**, then that user's **Credentials** tab → **Set password**.
2. **Clients** → **Create client** → give it a client ID (this is the same value you'll put in `MCP_OAUTH_AUDIENCE`). If this client will be used for a browser login flow, also enable **Standard flow** on its Settings tab, and add the caller's own OAuth callback to **Valid redirect URIs** (Claude's is `https://claude.ai/api/mcp/auth_callback`) — skip this and Keycloak lets the user log in successfully and only *then* rejects the redirect back, which looks like a login failure but isn't one.
3. Click into that client → **Client scopes** tab → open its `<client-id>-dedicated` scope.
4. **Add mapper** → **By configuration** → **Audience**, then set **Included Client Audience** to that same client — this is what actually stamps the `aud` claim; without it, issued tokens carry Keycloak's default `aud` (`account`) instead, and every request fails signature-adjacent-but-audience-mismatch with a 401.
5. Save, then confirm it worked by decoding a freshly-issued token (e.g. at jwt.io) and checking `aud` actually contains your client ID — a mapper that got saved with an empty audience field silently adds nothing, which is easy to miss.

One more gotcha specific to `MCP_OAUTH_RESOURCE_URL`: it has to be an address the *client* can actually reach — if that client is Claude, it's running in Anthropic's cloud, not on your machine. Pointing this at `localhost` means the 401 response's `WWW-Authenticate` header advertises an unreachable metadata URL, so OAuth discovery silently fails. If you're tunnelling a local dev server (e.g. ngrok), this needs to be the tunnel's actual public HTTPS URL, and it changes every time a free-tier tunnel restarts. Also, if you're running this behind docker-compose, `docker compose restart` reuses the container's original environment and won't pick up an updated `.env` — use `docker compose up -d` to recreate it instead.

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
      - "17080:8080"   # FastMCP Streamable HTTP port
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

## 🔗 External Client Setup Guide (HTTPS / Streamable HTTP)

How to connect securely from a PC outside your home server, through an Nginx reverse proxy and an SSL certificate on your home server's domain.

### 1. Nginx reverse proxy config (required)

So the streaming responses FastMCP's Streamable HTTP transport uses don't get cut off, make sure your domain's `proxy_pass` block maps to the RAG server's **port `17080`** and disables proxy buffering.

```nginx
server {
    listen 443 ssl;
    server_name mcp.your-domain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        # ⭐ Key part: forward to the MCP port (17080) exposed by docker-compose.
        proxy_pass http://localhost:17080;
        
        # Required to keep the streaming connection alive in real time
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

By default (`MCP_OAUTH_ENABLED` unset) the MCP server processes any request with no auth check at all — fine behind a VPN/private network, but anyone who reaches the URL directly can search your knowledge base, so put it behind something (reverse-proxy IP allowlist, VPN, etc.) if it's reachable from the open internet. In that mode, just point Claude Desktop at the URL:

```json
{
  "mcpServers": {
    "outline-knowledge-base": {
      "url": "https://mcp.your-domain.com/mcp"
    }
  }
}
```

If you turn on `MCP_OAUTH_ENABLED=true` (Keycloak), Claude Desktop's remote connector discovers the server's OAuth metadata itself and walks you through a normal browser login/consent flow the first time you connect — there's no static token to paste into the config. See `docs/keycloak-reference-compose.yml` for a throwaway Keycloak realm if you want to test this locally. Clients that can't run a browser-based OAuth flow can instead send an already-issued Keycloak access token directly as an `Authorization: Bearer <token>` header.

### 3. Claude Code (CLI) setup

With OAuth off, add it like any other remote HTTP server:

```bash
claude mcp add --transport http outline-knowledge-base https://mcp.your-domain.com/mcp
```

With `MCP_OAUTH_ENABLED=true`, skip Dynamic Client Registration and register a pre-configured client instead — Keycloak's default "Trusted Hosts" Client Registration Policy rejects anonymous DCR from hosts it doesn't already know about (a plain dev tunnel like ngrok will hit this), and Claude Code's own OAuth callback is a local loopback URL (`http://localhost:PORT/callback`), different from Claude Desktop's (`https://claude.ai/api/mcp/auth_callback`) — both need to be registered on the same Keycloak client if you use both.

1. In Keycloak, add `http://localhost:8080/callback` (or whichever port you pick below) to that client's **Valid redirect URIs**, alongside any other client's callback already there.
2. Register the server with that client's credentials:
   ```bash
   claude mcp add-json outline-knowledge-base \
     '{"type":"http","url":"https://mcp.your-domain.com/mcp","oauth":{"clientId":"your-client-id","callbackPort":8080}}' \
     --client-secret
   ```
   (prompts for the secret; it's stored in your system keychain/credential store, not in `.mcp.json`)
3. `claude mcp login outline-knowledge-base` (or `/mcp` inside a session) to run through the browser login.

---

## ⚙️ Manual Sync & Management API

For controlling indexing directly, beyond the background auto-sync (hourly by default).

* **Trigger an incremental sync**: `POST http://localhost:17000/sync/outline`
* **Force a full re-index**: `POST http://localhost:17000/sync/outline?full=true`
* **Check sync status**: `GET http://localhost:17000/sync/status`

---

If this open-source project has been useful to you, please support it with a ⭐️ **Star**! Feel free to leave any questions in the Issues tab.

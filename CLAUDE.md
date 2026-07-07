# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Outline → RAG → MCP: a service that watches a self-hosted [Outline](https://www.getoutline.com/) wiki, embeds its documents with Gemini, stores vectors in Qdrant, and exposes a `search_knowledge` MCP tool for AI agents. Ships as a single Docker image (`outline-mcp-vector`).

`createplan.md` is the original Korean design doc — useful for intent/rationale, but the code has moved on in places (it says HuggingFace/pluggable providers and `EMBEDDING_PROVIDER`; the real implementation is Gemini-only). Trust the code and `README.md`/`CONTRIBUTING.md` over `createplan.md` when they disagree.

## Commands

```bash
# Install (runtime + test deps)
pip install -r requirements.txt -r requirements-test.txt

# Run the full test suite (everything is mocked — no Redis/Qdrant/Outline/Gemini needed)
python -m pytest tests/ -v

# Run one file / one test
python -m pytest tests/test_chunker.py -v
python -m pytest tests/test_tasks.py::test_run_sync_skips_documents_not_changed_since_cursor -v

# Build the single image locally
docker build -t outline-mcp-vector .

# Run the full stack from source (assumes a real Outline instance reachable
# via OUTLINE_BASE_URL in .env; spins up its own dev-only Redis — see
# docker-compose.dev.yml)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Manual sync (once running)
curl -X POST http://localhost:17000/sync/outline              # incremental
curl -X POST "http://localhost:17000/sync/outline?full=true"  # force full re-embed
curl http://localhost:17000/sync/status
curl http://localhost:17000/health
```

## Architecture

### One image, four supervised processes

Everything ships as one Docker image built from the root `Dockerfile`. `supervisord.conf` runs four processes inside a single `outline-mcp-vector` container:

| Process | Entrypoint | Role |
|---|---|---|
| `fastapi` | `uvicorn indexer.main:app` | Webhook receiver, manual sync trigger, health check. **Only enqueues Celery tasks — never does the actual work itself.** |
| `celery_worker` | `celery -A indexer.celery_app worker` | Does the real work: fetch from Outline, chunk, embed, upsert to Qdrant. |
| `celery_beat` | `celery -A indexer.celery_app beat` | Enqueues an incremental sync every `SYNC_INTERVAL_SECONDS` (default 3600). |
| `mcp_server` | `python -m mcpserver.main` | `search_knowledge` tool over Streamable HTTP (port 8080), read-only path. |

This is a deliberate design principle (see `createplan.md` §13, `CONTRIBUTING.md`): if you need a new background job, add a `[program:...]` block to `supervisord.conf` rather than a new image/service. Don't reintroduce the old multi-image setup (there used to be separate `rag-indexer`/`rag-mcp` images and `indexer`/`worker`/`beat`/`mcp` compose services — that was deliberately collapsed).

**Gotcha**: `celery -A indexer.celery_app worker` only imports `indexer/celery_app.py`. Tasks live in `indexer/tasks.py`, so `celery_app.py` imports it explicitly at the *bottom* of the file (after `celery_app` is defined, to dodge a circular import). If you add a new task module, it needs the same treatment or the worker starts with an empty task registry — this exact bug shipped once (`docs/troubleshooting/celery-worker-tasks-not-registered.md`).

### Module boundaries

- `connector/` — `OutlineConnector`: talks to the Outline REST API (`documents.list/info`, `collections.list`) and yields `Document` dataclasses. Has a `base_url`/`public_url` split (see below).
- `shared/` — used by both `indexer` and `mcpserver`:
  - `embedder.py` — `GeminiProvider` only (no pluggable provider abstraction anymore). `GOOGLE_API_KEYS` is a round-robin pool; a failed request rotates to the next key and retries, raising only once every key has failed.
  - `vector_store.py` — thin Qdrant wrapper (collection `documents`, cosine distance).
  - `sync_state.py` — persists the incremental-sync cursor (a timestamp) to a JSON file at `SYNC_STATE_PATH` (mounted as the `sync_state` volume).
- `indexer/` — FastAPI app + all Celery machinery:
  - `main.py` — thin: signature verification, `.delay()` calls, health/status. No business logic.
  - `tasks.py` — `process_webhook_event` and `run_sync`, the two Celery tasks that do everything.
  - `celery_app.py` — Celery app config + beat schedule.
  - `sync_lock.py` — Redis-backed locks: `acquire_sync_lock`/`release_sync_lock` (prevents overlapping sync runs) and `doc_lock(doc_id)` (prevents a webhook event and a sync pass from racing on the *same document* — see below).
  - `pipeline.py` — chunk → embed → upsert for one `Document`.
  - `chunker.py` — markdown chunking.
- `mcpserver/` — `search_knowledge` FastMCP tool: embed query → `vector_store.search`. No write path. Streamable HTTP only (single `/mcp` endpoint). Auth is opt-in Keycloak/OAuth (`MCP_OAUTH_ENABLED`), open by default — see below.

### MCP auth gotcha

Auth here is opt-in, not a fixed gate: `MCP_OAUTH_ENABLED` (default `false`) decides whether the server requires a Keycloak-issued Bearer JWT at all. When `false`, `mcpserver/main.py` builds the module-level `mcp = FastMCP(...)` with `auth=None, token_verifier=None` — a fully open server, on purpose (this project doesn't run its own auth infrastructure, and plenty of deployments just keep this behind a private network/VPN instead). When `true`, `_build_oauth_settings()` requires `MCP_OAUTH_ISSUER_URL` / `MCP_OAUTH_RESOURCE_URL` / `MCP_OAUTH_AUDIENCE` to all be set (raises at import otherwise) and wires up `KeycloakTokenVerifier`, which checks the JWT signature against the realm's JWKS endpoint (`PyJWKClient`, no local key management) plus `iss`/`aud`/`exp`. This project is a Resource Server only — it never issues, stores, or introspects tokens itself, and it doesn't provision Keycloak (see the reference-only `docs/keycloak-reference-compose.yml` if you don't already run one). There used to be a static `MCP_AUTH_TOKENS` pool with a custom `TokenAuthMiddleware` that also accepted the token via `?token=` query param (for Claude Desktop's header-less `url` config) — that's gone; the SDK's native `AuthSettings`/`TokenVerifier` wiring only supports the standard `Authorization: Bearer` header, which is what the MCP auth spec actually requires.

**Streamable HTTP, not SSE**: the server used to expose the legacy dedicated-SSE transport (`/sse` + `/messages`), but that transport is deprecated MCP-spec-wide and current clients (Claude Desktop's remote connector included) warn on or refuse it. `mcp.streamable_http_app()` unifies everything onto a single `/mcp` endpoint (GET for the server-push stream, POST for JSON-RPC messages, same URL). External client configs must point at `.../mcp`, not `.../sse`.

**DNS rebinding protection is opt-in via env, not on by default**: the `FastMCP(...)` constructor in `mcpserver/main.py` is passed `transport_security=TransportSecuritySettings(...)`, built from `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` (comma-separated, both empty by default). The SDK only auto-enables this protection when bound to a loopback host, and this project binds `0.0.0.0` in production, so without those two env vars set, Host/Origin validation stays silently off — same as before this was added. Setting `MCP_ALLOWED_HOSTS` is what flips `enable_dns_rebinding_protection` on; an empty list with protection forced on would 421 every request, including the real deployment's own domain, so don't enable one without the other. See `docs/troubleshooting/mcp-transport-spec-gaps.md`.

### Sync model

Three triggers all enqueue the same `run_sync` Celery task: FastAPI startup (`lifespan`), Celery Beat (hourly by default), and the manual `/sync/outline` endpoint. `run_sync`:
1. Takes `acquire_sync_lock()` (Redis) so overlapping syncs no-op instead of double-processing.
2. Reads the last-synced-at cursor from `shared/sync_state.py`; documents whose `updated_at` is older are skipped (no full re-embed on every run).
3. Diffs Qdrant's known `doc_id`s against Outline's live document list to delete anything no longer in Outline.
4. Every individual index/delete call — from `run_sync` *and* from webhook-triggered `process_webhook_event` — goes through `doc_lock(doc_id)` first. This exists because a webhook edit and a sync pass can otherwise both decide to re-embed the same document at once, interleave their delete+upsert calls, and leave a stale version in Qdrant (real bug, see `docs/troubleshooting/webhook-sync-race-condition.md`). If you add a new place that calls `index_document`/`delete_document`, wrap it in `doc_lock` too.

### Networking: Outline network/Redis reuse

Production `docker-compose.yml` assumes `outline-mcp-vector` joins Outline's own Docker network (external network `outline-net`) and reuses Outline's existing Redis on a separate logical DB (`REDIS_URL=redis://redis:6379/1`) — there is intentionally no dedicated Redis container in prod. `docker-compose.dev.yml` overrides this for local dev (no real Outline network available): it neutralizes `outline-net`'s `external: true`, spins up a standalone dev Redis, and clears `OUTLINE_API_URL` so calls fall back to the public `OUTLINE_BASE_URL`.

This is why `connector/outline.py`'s `OutlineConnector` has two URLs, not one:
- `base_url` (`OUTLINE_API_URL`, falls back to `OUTLINE_BASE_URL`) — used for actual API calls; can be the internal hostname (`http://outline:3000`) when co-located.
- `public_url` (`OUTLINE_PUBLIC_URL`, falls back to `OUTLINE_BASE_URL`) — used to build the doc URLs returned in search results, which must always stay externally clickable. **Never point this at an internal hostname.**

### Qdrant env var gotcha

`docker-compose.yml` passes Qdrant's auth key as a *bare* env var name (`- QDRANT__SERVICE__API_KEY`, no `=${VAR:-}`) so the variable is entirely absent from the container when unset in `.env`. This is intentional and load-bearing: Qdrant treats an explicitly-set-but-empty key as "auth required, any value accepted," while the Python client skips sending the header for a falsy key — combined, that makes every request 401. The variable name itself is also intentionally identical to what Qdrant's own server expects (`service.api_key` → `QDRANT__SERVICE__API_KEY`), not a project-specific alias — don't rename it to something shorter.

### Chunking

`indexer/chunker.py` splits on H1/H2/H3 markdown headers, but a naive regex would also match `#`/`##` comment lines *inside fenced code blocks*. It counts ``` occurrences before each candidate match and only treats it as a real heading if that count is even (i.e. not inside an open fence). Don't simplify this away — it's a fix for a real bug, not defensive over-engineering.

### Testing conventions

Every external dependency is mocked — tests never need a running Redis/Qdrant/Outline/Gemini:
- Redis-backed locks (`indexer/sync_lock.py`) are tested against `fakeredis` (needs the `[lua]` extra installed for `redis.Redis.lock()`'s release script to work).
- `google.genai`/`google.genai.types` are faked by injecting stand-in modules into `sys.modules` before importing `GeminiProvider` (see `tests/test_embedder_gemini.py`).
- Celery tasks are tested by calling the task object directly (`tasks_module.run_sync(full=False)`), not `.delay()` — this runs the task body synchronously in-process with no broker needed.

See `CONTRIBUTING.md` for branch/commit conventions, the PR checklist, and how to add a new embedding provider if `GeminiProvider` ever needs a sibling. See `docs/troubleshooting/` for write-ups of real bugs found in this codebase (template at `docs/troubleshooting/template.md`).

테스트와 도커 빌드 같은 경우는 사용자에게 어떤 테스트가 필요한지 안내하고, 위임한다.

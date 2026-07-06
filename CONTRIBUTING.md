🌐 **Language**: **English** | [한국어](CONTRIBUTING.ko.md)

---

# Contributing

## Branch Strategy

All work branches off `main` and merges back via PR.

Branch name format

```
<type>/<slug>
```

| Type | Description | Example |
|------|------|------|
| feat | New feature | `feat/gemini-key-pool` |
| fix | Bug fix | `fix/chunker-code-block` |
| docs | Docs change | `docs/readme` |
| refactor | Refactor | `refactor/embedder` |
| test | Tests | `test/chunker` |
| ci | CI/CD | `ci/docker-publish` |
| chore | Misc | `chore/dependencies` |

Keep each branch to a single logical change.

---

## Commit Messages

Uses the [Conventional Commits](https://www.conventionalcommits.org/) format.

```
<type>: <description>
```

Examples

```
feat: support round-robin Gemini API key pool
fix: fix code blocks being split incorrectly
docs: improve README install instructions
```

---

## Pull Requests

Please check the following before opening a PR.

- [ ] Tests pass (`pytest`)
- [ ] Docker image builds and runs
- [ ] If you added a new env var, reflect it in `.env.example` and `README.md`
- [ ] Check whether you can reuse an existing tool's (Outline, Qdrant, etc.) env var name as-is

---

## Project Structure

```
outline-rag-mcp/
├── connector/      # Outline API
├── shared/         # Gemini, Qdrant, shared modules
├── indexer/        # Webhook / Sync / Celery
├── mcpserver/      # MCP Server
├── tests/
├── Dockerfile
├── supervisord.conf
└── docker-compose.yml
```

---

## Design Principles

- Ships as a single Docker image (`outline-mcp-vector`)
- Supervisor manages FastAPI, Celery Worker, Celery Beat, and the MCP Server
- Incremental sync driven by Outline webhooks
- Gemini (`gemini-embedding-2`) fixed
- Qdrant-based vector search

---

## Testing

```bash
pip install -r requirements.txt -r requirements-test.txt

pytest
```

Every external service is mocked, so this runs without Redis, Outline, Gemini, or Qdrant.

---
## Dev Environment
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d

```

---
## Troubleshooting

Write up bugs and production incidents in

```
docs/troubleshooting/
```


Prefer this order when writing one up:

- Problem
- Cause
- Fix
- Lessons learned

🌐 **Language**: [English](CONTRIBUTING.md) | **한국어**

---

# Contributing

## 브랜치 전략

모든 작업은 `main`에서 브랜치를 생성하여 PR로 병합합니다.

브랜치 이름 형식

```
<type>/<slug>
```

| Type | 설명 | 예시 |
|------|------|------|
| feat | 기능 추가 | `feat/gemini-key-pool` |
| fix | 버그 수정 | `fix/chunker-code-block` |
| docs | 문서 수정 | `docs/readme` |
| refactor | 리팩터링 | `refactor/embedder` |
| test | 테스트 | `test/chunker` |
| ci | CI/CD | `ci/docker-publish` |
| chore | 기타 작업 | `chore/dependencies` |

하나의 브랜치에는 하나의 논리적인 변경만 포함하는 것을 권장합니다.

---

## 커밋 메시지

[Conventional Commits](https://www.conventionalcommits.org/) 형식을 사용합니다.

```
<type>: <description>
```

예시

```
feat: Gemini 다중 API 키 순환 지원
fix: 코드 블록이 잘못 분리되는 문제 수정
docs: README 설치 방법 개선
```

---

## Pull Request

PR 전 아래 항목을 확인해주세요.

- [ ] 테스트 통과 (`pytest`)
- [ ] Docker 이미지 빌드 및 실행 확인
- [ ] 새로운 환경변수를 추가했다면 `.env.example`과 `README.md` 반영
- [ ] 기존 도구(Outline, Qdrant 등)의 환경변수 이름을 그대로 사용할 수 있는지 검토

---

## 프로젝트 구조

```
outline-rag-mcp/
├── connector/      # Outline API
├── shared/         # Gemini, Qdrant, 공통 모듈
├── indexer/        # Webhook / Sync / Celery
├── mcpserver/      # MCP Server
├── tests/
├── Dockerfile
├── supervisord.conf
└── docker-compose.yml
```

---

## 설계 원칙

- 단일 Docker 이미지(`outline-mcp-vector`)로 배포
- Supervisor가 FastAPI, Celery Worker, Celery Beat, MCP Server를 관리
- Outline Webhook 기반 증분 동기화
- Gemini(`gemini-embedding-2`) 고정
- Qdrant 기반 벡터 검색

---

## 테스트

```bash
pip install -r requirements.txt -r requirements-test.txt

pytest
```

외부 서비스는 모두 Mock으로 대체되어 Redis, Outline, Gemini, Qdrant 없이 실행할 수 있습니다.

---
## 개발환경
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d

```

---
## 트러블슈팅

버그 해결 과정이나 운영 중 발생한 이슈는

```
docs/troubleshooting/
```


가능하면 다음 순서로 작성합니다.

- 문제
- 원인
- 해결 방법
- 배운 점

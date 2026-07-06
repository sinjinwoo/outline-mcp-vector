## 1. 문제 요약

* `shared/embedder.py`가 쓰던 `google.generativeai`(구 SDK)로 임베딩 호출 시 `import` 시점부터 지원 종료(FutureWarning)가 뜨고, 실제 `embed_content` 호출은 전부 404로 실패
* 사용자가 직접 지적: "from google import genai / from google.genai import types 이걸로 바꿔야할듯 지금 import google.generativeai as genai 는 에러나는데?"
* 재현:

  ```python
  import google.generativeai as genai
  genai.configure(api_key=key)
  genai.embed_content(model="models/gemini-embedding-002", content="hello world", task_type="RETRIEVAL_QUERY")
  # google.api_core.exceptions.NotFound: 404 models/gemini-embedding-002 is not found for API
  # version v1beta, or is not supported for embedContent.
  ```

---

## 2. 원인

* `google-generativeai` 패키지 자체가 지원 종료됨 — v1beta API에 gemini-embedding 계열 모델이 더 이상 서빙되지 않아 키가 유효해도 무조건 404
* 신규 SDK(`google-genai`)로 바꿔도 하드코딩했던 모델명 `gemini-embedding-002`는 여전히 404 — 실제 키로 `client.models.list()`를 찍어보니 이 프로젝트에 서빙되는 이름은 `gemini-embedding-001` / `gemini-embedding-2-preview` / `gemini-embedding-2` 세 가지뿐, `-002`라는 이름 자체가 존재하지 않았음
* 세 모델 중 어떤 걸 기본값으로 할지 시행착오: 처음엔 환경변수로 통제 가능하게 만들었다가("API 키/프로젝트마다 다를 수 있으니"), `-2`와 `-2-preview`가 같은 입력에 대해 완전히 동일한 벡터를 반환하는 걸 확인한 뒤 "그럼 그냥 안정 버전인 `-2`만 지원하자"로 방향을 바꿔서 다시 하드코딩으로 되돌림 (환경변수 통제는 불필요한 유연성이었음)
* 여기서 한 걸음 더: Google이 `gemini-embedding-2`에 대해 권장하는 사용법은 구 모델들의 `task_type` API 파라미터 방식이 아니라, **입력 텍스트 자체에 작업 접두사를 박아 넣는 방식**(`task: search result | query: ...` / `title: ... | text: ...`)이라는 걸 사용자가 공식 가이드를 인용해 지적 — 기존 코드는 여전히 구식 `task_type=RETRIEVAL_QUERY/RETRIEVAL_DOCUMENT` 방식만 쓰고 있었음

---

## 3. 해결 과정

1. **SDK 교체**: `from google import genai` / `from google.genai import types`로 전환. 신규 SDK는 전역 `genai.configure()`가 없는 client 기반 구조라, 키 풀 로직도 "키마다 `genai.Client(api_key=key)` 하나씩 미리 만들어두고 라운드로빈으로 골라 쓰는" 방식으로 바꿈

   ```python
   self._clients = [genai.Client(api_key=key) for key in keys]
   ...
   result = client.models.embed_content(
       model=_MODEL, contents=text,
       config=self._types.EmbedContentConfig(output_dimensionality=self._dimension),
   )
   return result.embeddings[0].values
   ```

2. **모델명 확정**: `client.models.list()`로 실제 서빙되는 임베딩 모델을 확인 → `gemini-embedding-2`로 하드코딩(환경변수 통제 도입했다가 되돌림)
3. **비대칭 검색 포맷 적용**: 쿼리와 문서를 서로 다른 접두사로 임베딩

   ```python
   def _prepare_query(text: str) -> str:
       return f"task: search result | query: {text}"

   def _prepare_document(text: str, title: str | None) -> str:
       return f"title: {title or 'none'} | text: {text}"
   ```

   `embed_passages`에 `title` 파라미터를 추가하고 `indexer/pipeline.py`에서 `embed_passages(chunks, title=doc.title)`로 실제 문서 제목을 넘기도록 연결
4. **실제 API로 검증** (모킹이 아니라 `.env`의 진짜 키로): 쿼리/문서 임베딩이 정상적으로 3072차원을 반환하는지, `gemini-embedding-2`와 `-2-preview`가 동일 벡터를 내는지 직접 호출해서 확인
5. **A/B 테스트로 프리픽스의 실제 효과 검증**: `scripts/ab_test_embedding_prefix.py`를 새로 작성 — 실제 Outline 문서 몇 개를 "프리픽스 적용" / "프리픽스 미적용" 두 개의 임시 Qdrant 컬렉션에 각각 넣고, 도메인에 맞는 실제 질의(예: "실습코치 지원 자격 요건이 뭐야")로 검색해서 순위를 나란히 비교
   * 처음엔 영어로 된 일반적인 샘플 쿼리("how do I get started" 등)로 돌렸는데 실제 문서 내용(면접 준비/실습코치 지원 관련 한국어 콘텐츠)과 주제가 안 맞아 신호가 약했음 — 실제 콘텐츠에 맞는 한국어 질의로 다시 돌리고 나서야 의미 있는 차이가 드러남
   * 결과: 질의가 "이 문서/카테고리에서 답을 찾아줘" 유형일 때 프리픽스 버전이 다른 주제의 문서가 섞여 들어오는 걸 확실히 줄여줌. 이미 충분히 구체적인 질의에서는 차이가 거의 없었음 → 프리픽스 버전을 프로덕션 기본값으로 확정

## 🧪 Embedding Prefix A/B Test

`gemini-embedding-2`에서 권장하는 **Query/Document Prefix**가 실제 검색 성능에 미치는 영향을 검증하기 위해 A/B 테스트를 수행했습니다.

### 실험 방법

동일한 Outline 문서를 두 개의 Qdrant 컬렉션에 각각 인덱싱했습니다.

| Collection | Document Format |
|------------|-----------------|
| **WITH Prefix** | `title: {title} \| text: {content}` |
| **WITHOUT Prefix** | `{content}` |

검색 시에도 동일한 방식으로 Query를 생성했습니다.

| Query Type | Query Format |
|------------|--------------|
| **WITH Prefix** | `task: search result \| query: {question}` |
| **WITHOUT Prefix** | `{question}` |

즉, **문서, Chunk, 검색어는 모두 동일**하며 **Embedding 입력 형식(Prefix)만 변경**하여 비교했습니다.

---

## 📈 검색 결과 비교

| Query | WITH Prefix | WITHOUT Prefix |
|------|-------------|----------------|
| **실습코치 지원 자격 요건이 뭐야** | 관련 문서 Top5 검색 | 인성면접 문서가 1위 |
| **기술 면접에서 CS 질문 어떤 게 나와** | CS 문서 중심 검색 | 일부 인성면접 문서 혼합 |
| **자소서 자기소개 어떻게 써야 해** | 자소서 문서가 안정적으로 상위 | 비슷하지만 관련도 낮은 문서 포함 |
| **인성면접에서 갈등 상황 대처 질문에 어떻게 답해** | 관련 답변만 검색 | 점수 하락 및 일부 노이즈 발생 |

### Example

#### Query

```text
실습코치 지원 자격 요건이 뭐야
```

| WITH Prefix | Score |
|-------------|------:|
| 실습코치 지원 > 자소서 | **0.8138** |
| 실습코치 지원 > 공통 | 0.7777 |
| 실습코치 지원 > 특화 | 0.7616 |

↓

| WITHOUT Prefix | Score |
|---------------|------:|
| ❌ 인성면접 준비 | **0.5392** |
| 실습코치 지원 > 자소서 | 0.5320 |
| 인성면접 준비 | 0.5186 |

---

## ✅ 결론

Embedding 생성 시 Query와 Document에 역할을 나타내는 Prefix를 추가하면

- 검색 점수(Similarity)가 전반적으로 상승
- 검색 의도와 맞지 않는 문서가 상위에 노출되는 현상 감소
- 질문 유형별 관련 문서가 더 안정적으로 검색

실험 결과, **Gemini Embedding의 권장 Prefix를 적용하는 것이 RAG 검색 품질 향상에 효과적**임을 확인했습니다.


---

## 4. 배운 점

* **절대 점수(코사인 유사도) 상승만으로 "더 좋아졌다"고 판단하면 안 된다.** 프리픽스를 붙이면 쿼리·문서 양쪽에 공통 텍스트(`task:`, `title:` 등)가 들어가면서 유사도 점수 자체가 전반적으로 밀려 올라가는 아티팩트가 생긴다. 봐야 할 건 점수 크기가 아니라 순위(랭킹)가 실제로 더 정확해졌는지다
* **모델/SDK가 권장하는 사용법 문서를 코드가 아니라 실제 API 응답으로 검증해야 한다.** 하드코딩된 모델명(`-002`)은 실제로 존재하지도 않는 이름이었고, `client.models.list()`처럼 "지금 이 키로 뭐가 되는지" 직접 확인하는 절차 없이는 이런 문제를 코드 리뷰만으로 잡아낼 수 없었음
* **A/B 테스트는 쿼리셋이 실제 도메인과 맞아야 신호가 나온다.** 일반적인 영어 샘플 쿼리로는 두 방식의 차이가 거의 안 보였는데, 실제 문서 주제에 맞는 질의로 바꾸자마자 명확한 차이가 드러났다 — 테스트 설계 자체가 잘못되면 "차이 없음"이라는 잘못된 결론에 이를 수 있다
* **불필요한 설정 가능성은 오히려 복잡도다.** 모델명을 환경변수로 빼두는 게 "더 유연해 보였지만", 실제로는 후보가 사실상 하나(`-2`)로 좁혀지자 바로 하드코딩으로 되돌렸다 — 유연성은 실제로 바뀔 가능성이 있는 값에만 줄 가치가 있다

---

## 5. 후속 조치 (Optional)

* [x] `shared/embedder.py`를 `google-genai` SDK + `gemini-embedding-2` 하드코딩으로 마이그레이션
* [x] 비대칭 검색 프리픽스(`_prepare_query`/`_prepare_document`) 적용, `embed_passages`에 `title` 파라미터 추가
* [x] `requirements.txt`: `google-generativeai` → `google-genai`
* [x] `tests/test_embedder_gemini.py`: 신규 SDK 모킹 + 프리픽스 포맷 검증으로 재작성
* [x] `scripts/ab_test_embedding_prefix.py` 작성, `docker-compose.dev.yml`에 Qdrant 포트 퍼블리시 추가
* [x] 실제 도메인 질의로 A/B 테스트 실행, 프리픽스 버전이 문서 간 혼선을 줄인다는 것 확인
* [ ] 기존에 이미 인덱싱된 문서(`documents` 컬렉션)는 여전히 프리픽스 적용 전 벡터를 갖고 있음 — 증분 동기화는 `updated_at` 기준으로 스킵되므로 반영 안 됨. `POST /sync/outline?full=true`로 전체 재임베딩 필요 (아직 실행 안 함)
* [ ] `gemini-embedding-2`가 프리뷰 딱지 없는 정식 모델인지, 아니면 언젠가 이름이 또 바뀔 수 있는지는 Google 쪽 공지를 계속 지켜봐야 함

## 1. 문제 요약

* 검색 결과가 "의미 단위로 안 잘리고 파편화된 것처럼 보인다"는 지적에서 시작해, 실제로는 서로 다른 네 가지 원인이 섞여 있었음을 확인하고 각각 고침:
  1. `shared/vector_store.py`의 `search()`가 `chunk_text`를 무조건 `[:500]`으로 잘라 반환하던 문제
  2. `indexer/chunker.py`가 헤더(H1~H3)마다 무조건 자르기만 하고, 너무 작은 섹션을 이웃과 합치는 로직이 없어 소제목이 잦은 문서가 파편화되던 문제
  3. 마크다운 표/리스트가 슬라이딩 윈도우 분할 중 행/항목 중간에서 잘릴 수 있던 문제
  4. 이미지(`![alt](url)`)가 전혀 처리되지 않아, 원본 첨부파일 URL 문자열이 그대로 청킹·임베딩·검색 결과에 노출되던 문제

---

## 2. 원인

* **프리뷰 절단**: `chunk_markdown()`은 헤더 단위로 먼저 자르고 그 섹션이 ~3200자(`CHUNK_SIZE`)를 넘을 때만 추가로 쪼개므로, 일반적인 섹션 하나는 청크 경계 자체는 온전한 경우가 많았다. 실제로 내용이 잘려나가는 지점은 청킹이 아니라 `search()`의 500자 프리뷰 절단이었다 — 사용자가 직접 이 지점을 짚어냈다.
* **소섹션 미병합**: `chunk_markdown`에 "섹션이 크면 추가로 쪼개는" 로직만 있고 "섹션이 작으면 합치는" 로직이 없어서, 소제목마다 한두 문장뿐인 문서는 그대로 파편화된(문맥이 좁은) 청크가 됐다.
* **표/리스트 미보호**: `_split_by_size`의 슬라이딩 윈도우가 우선 `\n\n`(문단 구분)을 찾고 없으면 아무 `\n`으로 폴백하는데, 마크다운 표나 리스트는 항목/행 사이가 보통 단일 `\n`이라 이 폴백에 걸리면 표의 헤더 행과 본문 행 사이, 혹은 리스트 항목 중간에서 잘릴 수 있었다. 코드펜스는 이미 `_split_by_headers`에서 "펜스 카운트 짝수 여부"로 보호되고 있었지만 표/리스트에는 그런 보호가 없었다.
* **이미지 미처리**: Outline 마크다운 원문의 `![alt](url)` 태그가 아무 필터링 없이 `chunk_markdown` → `embed_passages` → 저장까지 그대로 흘러갔다. 실제 이미지 내용은 임베딩에 전혀 반영되지 않고, 의미 없는 첨부파일 URL 문자열만 청크 예산을 갉아먹으며 노이즈로 섞였다.

---

## 3. 해결 과정

* `shared/vector_store.py`의 `snippet`을 절단 없이 `chunk_text` 전체로 변경 — 청킹 단계에서 이미 ~3200자로 상한을 둔 값을 검색 레이어에서 또 자를 이유가 없었다.
* `indexer/chunker.py`에 `_merge_small_chunks()` 추가: `MIN_CHUNK_SIZE`(~200토큰) 미만인 청크만 다음 청크와 합치되, 조건이 "현재 버퍼가 작은가"이지 "다음 청크가 작은가"가 아니어서 이미 적당히 큰 섹션은 억지로 합쳐지지 않는다.
* `_find_atomic_blocks()`/`_snap_out_of_atomic_block()` 추가: 연속된 리스트 항목·표 행 구간을 하나의 "쪼개면 안 되는 블록"으로 인식해서, `_split_by_size`가 계산한 분할 지점이 블록 중간에 떨어지면 블록 시작 전으로 당기거나(가능하면) 블록 끝까지 포함시켜(`CHUNK_SIZE`를 다소 넘기더라도) 표/리스트를 통째로 보존한다.
* 이미지 처리는 사용자가 `https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api?hl=ko`를 근거로, Gemma 모델이 기존 프로젝트가 이미 쓰고 있는 `google-genai` SDK/Gemini API를 통해 멀티모달을 지원한다는 걸 확인하면서 방향이 잡혔다 — 새 서비스/인프라 없이 `shared/embedder.py`의 기존 API 키 풀(`GOOGLE_API_KEYS`, `_call_with_key_rotation`)을 그대로 재사용.
  * 처음에는 Google 문서의 `client.files.upload(file=...)` + `client.models.generate_content(contents=[uploaded, prompt])` 예시를 그대로 따라 구현했다(설치된 SDK가 파일 경로만 받을 가능성까지 고려해 temp file 폴백도 넣었음).
  * 이어서 사용자가 실제 Gemini API의 **인라인 base64 이미지 전달** 방식(`client.interactions.create(model=..., input=[{"type":"text",...}, {"type":"image","data": base64...,"mime_type":...}])`, `interaction.output_text`)을 근거와 함께 제시 — Files API 업로드 왕복 자체가 필요 없는 훨씬 단순한 경로였다. `caption_image()`를 이 방식으로 교체해서 업로드 스텝과 temp file 폴백 코드를 통째로 제거했다.
  * `indexer/image_captions.py`를 새로 만들어 `![alt](url)` 태그를 정규식으로 찾고, Outline과 같은 origin이면 인증 헤더를 붙여 다운로드(외부 호스트면 헤더 없이), 캡션으로 치환. 다운로드/캡셔닝 실패는 그 이미지 하나만 alt 텍스트로 폴백(없으면 제거)하고 문서 전체 색인은 막지 않는다.
  * 재캡셔닝 캐시는 별도로 두지 않기로 했다 — "이미지가 바뀌었다는 건 문서가 바뀌었다는 뜻"이라는 사용자 판단대로, 문서가 재색인될 때(웹훅/주기 동기화 모두) 이미지도 그때마다 다시 캡셔닝한다. 이는 지금도 문서의 다른 부분만 바뀌어도 전체 청크가 재임베딩되는 것과 같은 성격의 트레이드오프다.
* 회귀 테스트: `tests/test_chunker.py`(병합/표·리스트 보호 각 케이스, 기존 헤더 분할 테스트는 병합 로직과 우연히 충돌해 픽스처 크기를 키워 조정), 신규 `tests/test_vector_store.py`(스니펫 비절단), `tests/test_embedder_gemini.py`(캡셔닝 3종 — 모델/입력 형식, 키 로테이션, 모듈 레벨 함수), 신규 `tests/test_image_captions.py`(태그 치환, origin별 인증 헤더, 실패 폴백, 다른 이미지에 영향 없음) 추가.

---

## 4. 배운 점

* "파편화돼 보인다"는 증상 하나에 원인이 여러 개 겹쳐 있을 수 있다 — 프리뷰 절단(표시 레이어)과 청킹 설계 공백(저장/검색 레이어)은 서로 다른 문제였고, 하나만 고치면 나머지가 여전히 남는다.
* 청킹처럼 "쪼개는" 로직을 만들 때는 "너무 크면 쪼갠다"만 생각하기 쉽지만, "너무 작으면 합친다"도 대칭적으로 필요하다. 한쪽만 있으면 입력 분포에 따라 반대쪽 극단(과도한 파편화 또는 과도한 병합)이 방치된다.
* 코드펜스처럼 "쪼개면 안 되는 구간"이 하나 있다는 걸 알면, 같은 종류의 구간(표, 리스트)이 더 있는지 의심해봐야 한다 — 보호 로직이 특정 케이스 하나에만 좁게 적용돼 있으면 구조적으로 비슷한 다른 케이스는 놓치기 쉽다.
* 외부 SDK의 정확한 호출 시그니처는 추측하지 말고 실제 공식 문서/예시로 확인해야 한다 — Files API 업로드 방식으로 먼저 구현했지만, 실제로는 인라인 base64 전달이 더 간단하고 정확한 방법이었다. 문서 없이 짐작한 구현은 "일단 동작할 것 같은" 코드이지 "맞는" 코드가 아니다.

---

## 5. 후속 조치 (Optional)

* [x] `shared/vector_store.py`의 `search()` 스니펫 절단 제거
* [x] `indexer/chunker.py`에 소섹션 병합(`_merge_small_chunks`) 및 표/리스트 보호(`_find_atomic_blocks`/`_snap_out_of_atomic_block`) 추가
* [x] `shared/embedder.py`에 `caption_image()` 추가 (인라인 base64, `client.interactions.create`)
* [x] `indexer/image_captions.py` 신규 작성 및 `indexer/pipeline.py`에 연결
* [x] 관련 단위 테스트 추가/보강
* [ ] 실제 Gemini API 키로 `caption_image()`가 진짜 이미지에 대해 의미 있는 캡션을 반환하는지 수동 확인 (mock 테스트만으로는 실제 모델 응답 품질까지 검증 못 함)
* [ ] 배포 후 `curl -X POST "http://localhost:17000/sync/outline?full=true"`로 전체 재색인 — 청킹/캡셔닝 로직이 바뀌었으므로 기존 색인은 예전 상태로 남아있음
* [ ] 소제목이 잦은 문서, 표/리스트가 있는 문서, 이미지가 포함된 문서 각각에 대해 재색인 후 `search_knowledge`로 육안 확인

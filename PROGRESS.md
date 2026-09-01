# 진행 상태

마지막 갱신: 2026-09-01

## 요약

**0~8단계 전체 end-to-end 1회 실행 성공.** 뉴스 수집 → 정제/중복제거 →
텔레그램 승인(운영자가 실제 버튼 클릭) → 프롬프트 변환 → FLUX 배경 이미지
생성 → 비전 모델 검증 → Pillow 카드 합성까지 실제 계정으로 전부 돌려서
카테고리당 1장씩 총 3장을 `output/2026-09-01/`에 생성함. 과정에서 발견한
버그 3건은 모두 수정 완료(아래 "오늘 발견/수정한 버그" 참고).

## 단계별 완료 여부

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 저장소 초기화 | 완료. GitHub 원격 저장소(`Dajeong0315/ai-news-pipeline`) 연결 및 push까지 완료 |
| 1 | 개발 환경 준비 | 완료 |
| 2 | 뉴스 수집 스크립트 | **완료, 실 Supabase로 검증됨** (실행 1회 4611건 저장) |
| 3 | 정제 로직 | **완료, 실 Supabase로 검증됨** (버그 수정 후 재검증) |
| 4 | 텔레그램 승인 시스템 | **완료, 실 텔레그램/Vercel로 end-to-end 검증됨** (버튼 클릭 → Supabase 반영 확인) |
| 5 | 프롬프트 변환 | **완료, 실 Cloudflare API로 검증됨** |
| 6 | 이미지 생성 (FLUX) | **완료, 실 Cloudflare API로 검증됨** |
| 7 | 이미지 검증 | **완료, 실 Cloudflare API로 검증됨** (모델 교체 후) |
| 8 | 카드 합성 | **완료, 실제 카드 3장 생성 확인** (한글 렌더링/제목 축약 정상) |

## 오늘 발견/수정한 버그

1. **`clean_news.py`가 Supabase 1000행 제한에 걸려 뒤쪽 카테고리를 누락함.**
   PostgREST는 기본적으로 한 번에 최대 1000행만 반환하는데, 페이지네이션 없이
   전체를 가져온다고 가정한 게 원인. `collect_news.py`가 index_macro →
   stock → policy_industry 순으로 저장하다 보니 첫 1000행이 거의 index_macro로
   채워져, 실제 1차 실행에서 index_macro 345건이 필터링된 반면 stock은 1건,
   policy_industry는 0건만 통과함. `range()`로 전체를 순회하도록 수정(커밋
   `d9f672e`). 같은 종류의 카운트 쿼리를 짤 때도 이 제한을 항상 염두에 둘 것.
2. **비전 검증 모델 `@cf/google/gemma-3-12b-it`가 이 Cloudflare 계정 카탈로그에
   존재하지 않음** (스펙에 적힌 "Gemma Vision"을 문자 그대로 썼던 것이 원인).
   403 Forbidden 발생. `GET /ai/models/search`로 실제 계정에서 쓸 수 있는
   모델을 확인한 결과, 대안으로 시도한 `@cf/meta/llama-3.2-11b-vision-instruct`는
   Meta 커뮤니티 라이선스 동의(거주지 진술 포함)가 필요해 사용자 동의 없이
   임의로 넘기지 않기로 함. 최종적으로 별도 동의가 필요 없는
   `@cf/llava-hf/llava-1.5-7b-hf`로 교체(커밋 `ba70519`). 입력 포맷도
   `messages`+`image_url`이 아니라 `{"image": [...byte array...], "prompt": str}`,
   출력도 `result.description`으로 LLaVA 스펙에 맞게 재작성함.
3. **GLM API가 결제수단 등록을 요구해 가입 불가** → Cloudflare Workers AI
   텍스트 모델(`@cf/meta/llama-3.1-8b-instruct`)로 대체(커밋 `99f9b5d`,
   자세한 내용은 아래 "프롬프트 변환 관련 설계 결정" 참고).

## 외부 서비스 계정/키 발급 상태

값 자체는 절대 여기 기록하지 않고 `.env`에만 저장한다. 아래는 "발급 완료" 여부만 표시.

| 서비스 | 계정 생성 | API 키/토큰 발급 | 비고 |
|---|---|---|---|
| Telegram Bot (BotFather) | 완료 | 완료 | 봇: `@ainews_card_bot` |
| Supabase | 완료 | 완료 | 스키마 적용 및 실사용 확인 |
| Vercel | 완료 | 완료 | `webhook/` 배포 완료, URL: `https://ai-news-pipeline-mu.vercel.app/webhook`, 텔레그램 webhook 등록 및 실제 버튼 클릭 동작 확인 |
| Cloudflare (Workers AI) | 완료 | 완료 | FLUX 이미지 생성 + LLaVA 비전 검증 + 프롬프트 변환 텍스트 모델까지 이 계정 하나로 공용 |
| ~~GLM API (Zhipu/Z.AI)~~ | 사용 안 함 | - | 무료 티어가 결제수단 등록을 요구해 **Cloudflare Workers AI 텍스트 모델로 대체** |
| GitHub (원격 저장소) | 완료 | - | `Dajeong0315/ai-news-pipeline` (private), 브랜치 `main`, 매 커밋마다 push 진행 |

## 확인한 무료 티어 한도 (2026-09 기준)

### Cloudflare Workers AI
- 매일 **10,000 뉴런(Neurons)** 무료 (Workers Free 플랜, 매일 00:00 UTC 리셋). 초과분은 1,000 뉴런당 $0.011.
- 실사용 1회(FLUX 3회 + 텍스트 프롬프트 3회 + LLaVA 검증 3회, 총 9회 API 호출)로는 계정이 차단/한도 초과되지 않음을 확인. 정확한 뉴런 소모량 수치는 Cloudflare 대시보드에서 직접 확인 필요(API 응답 자체에는 뉴런 소모량이 안 찍힘).
- 소스: [Cloudflare Workers AI Free Tier 2026](https://pricepertoken.com/endpoints/cloudflare/free), [Cloudflare 공식 Pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)

### GLM API (Zhipu / Z.AI) — 사용 중단
- 웹 검색으로는 무료라는 정보가 나왔으나 **실제 가입 시 결제수단 등록을 요구**하는 것으로 확인됨(2026-09-01, 운영자 실제 가입 시도 결과). Cloudflare Workers AI 텍스트 모델로 대체.

### Supabase
- 무료 플랜: DB 500MB, egress 5GB, 파일 스토리지 1GB, edge function 호출 50만회/월, 활성 프로젝트 2개까지. 무료 프로젝트는 7일간 활동 없으면 자동 일시정지.
- 실사용 1회로 4611건 저장 + 수백 건 UPDATE/PATCH 수행, 문제 없었음.

### Vercel (Hobby 플랜)
- 서버리스 함수 호출 100만회/월, 최대 실행시간 60초. `webhook/api/index.py`는 승인/거절 처리만 하고 무거운 작업이 없어 이 제한에 안전.
- 실제 배포·webhook 등록·버튼 클릭 → 응답까지 정상 동작 확인.

### Telegram Bot API
- 무료, 유의미한 사용량 상한 없음. 실사용 20건 이상 메시지 전송, 문제 없었음.

**결론**: 하루 3장 규모는 Cloudflare/Supabase/Vercel/Telegram 무료 한도 안에 문제없이 들어감(실사용으로 확인).

## 카테고리 분류 로직 판단 기준

- 수집 단계(`collect_news.py`)에서는 검색어(keyword) 자체가 속한 카테고리를 그대로 `category` 컬럼에 채우는 방식 채택. 이유: Google News RSS 검색 결과가 대체로 검색어 주제와 일치하고, MVP 단계에서 별도 분류 모델을 두는 것은 과설계로 판단.
- 실사용 1회 결과, `rapidfuzz` 중복제거는 889건 후보 중 22건만 그룹핑됨(2.5%) — 임계값 85가 다소 엄격해 같은 사건의 다른 표현 제목을 못 잡는 경우가 있을 수 있음. 완료 기준의 "의도적 중복 2건 테스트"로 임계값 적정성을 추가 검증 필요(다음 세션 TODO).

## 정제 로직(clean_news.py) 관련 설계 결정

- `news_items`에 `summary` 컬럼 추가(원 DDL에는 없던 컬럼).
- **한계**: Google News RSS의 `summary`는 실제 본문 요약이 아니라 "제목 + 언론사명" 재구성 문자열 — `MIN_LENGTH` 필터는 사실상 "제목이 지나치게 짧은 저품질 기사" 제거 용도.
- **페이지네이션 필수**: PostgREST 기본 1000행 제한 때문에 `fetch_collected()`는 `range()`로 전체를 순회함(오늘 발견한 버그, 위 참고). 앞으로 이 프로젝트에서 Supabase 조회 코드를 새로 짤 때 결과가 많을 수 있는 쿼리는 항상 페이지네이션을 넣을 것.
- 중복 제거는 카테고리 내에서만 수행(그리디 클러스터링, `DEDUP_SIMILARITY_THRESHOLD`=85). 대표 기사는 그룹 내 `published_at`이 가장 이른 기사.

## 텔레그램 승인 시스템 관련 설계 결정

- 웹훅(`webhook/api/index.py`)은 Vercel 서버리스 함수의 60초 제한과 콜드스타트를 고려해, Supabase REST API를 `httpx`로 직접 호출하는 독립 모듈로 작성. `webhook/` 디렉터리를 Vercel 프로젝트 루트로 배포.
- "카테고리당 이미 승인된 건이 있으면 안내만" 로직은 `news_items.collected_at`이 한국시간 기준 오늘 자정 이후이고 `status='approved'`인 항목이 해당 카테고리에 있는지로 판단.
- 실사용 확인: 승인 버튼 클릭 시 Supabase `news_items.status` / `approval_requests.decision`이 정상 갱신됨. 이미 승인된 카테고리에 추가로 온 후보들은 `status='pending_approval'`로 그대로 남아 방치되며(스펙이 요구한 "무시" 동작), 파이프라인 하위 단계는 `status='approved'` 1건만 보므로 문제없음.

## 프롬프트 변환 관련 설계 결정 (GLM → Cloudflare 전환)

- `generate_prompt.py`는 `config.CLOUDFLARE_TEXT_MODEL`(기본값 `@cf/meta/llama-3.1-8b-instruct`)로 FLUX/비전 검증과 동일 Cloudflare 계정에 텍스트 생성을 요청.
- `verify_image.py`의 재시도 경로(`call_llm`)도 동일 함수 재사용.

## 이미지 생성/검증/합성 관련 설계 결정

- **비전 검증 모델은 `@cf/llava-hf/llava-1.5-7b-hf`**(`config.VISION_MODEL`). 원래 스펙의 "Gemma Vision"은 이 계정 카탈로그에 없고, 대안 Llama Vision 모델은 라이선스 동의(거주지 진술 포함)가 필요해 배제. LLaVA는 `{"image": [...], "prompt": str}` 입력, `result.description` 출력 형식이며 자유 서술형 답변을 하므로 "no"가 단독 단어로 등장하고 "yes"가 없을 때만 부적합으로 판정(모호하면 통과시키는 안전한 기본값).
- 카드 합성(`compose_card.py`)은 Windows 기본 한글 폰트(맑은 고딕) 사용. 다른 OS/서버 배포 시 `.env`의 `FONT_PATH`/`FONT_BOLD_PATH` 재설정 필요.
- **실사용 검증 완료**: FLUX 배경 이미지가 카테고리 분위기에 맞게(지수/거시=상승 그래프 느낌, 개별종목=디지털/기업 이미지, 정책/산업=도시 스카이라인) 생성되고, LLaVA 검증 3/3 통과, 카드 3장 모두 한글 텍스트 정상 렌더링 확인.

## 남은 이슈 / TODO

1. **`DEDUP_SIMILARITY_THRESHOLD`(현재 85) 적정성 검증** — 완료 기준의 "동일 사건 기사 2건 의도적 투입 → dedup_group_id 그룹핑 확인" 테스트로 실측 필요. 실사용에서 그룹핑률이 낮게 나온 점(2.5%) 고려.
2. **카테고리 자동 분류 정확도 샘플 10건 수동 검토**(완료 기준 항목) — 아직 미실시.
3. Cloudflare 뉴런 실제 소모량은 대시보드(Cloudflare 콘솔 → Workers AI → Usage)에서 직접 확인 권장 — API 응답 자체에는 안 찍힘.
4. index_macro 카테고리에 후보가 중복 전송된 건(오늘 버그로 인한 1회성 이슈)이 `pending_approval` 상태로 여러 건 남아있음 — 동작에는 지장 없으나 신경 쓰이면 텔레그램에서 거절 버튼으로 정리 가능.
5. 매일 자동 스케줄 실행(예: Windows 작업 스케줄러/cron)은 MVP 범위 밖 — 현재는 `run_daily.py collect` / `run_daily.py publish`를 수동 실행.

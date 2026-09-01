# 진행 상태

마지막 갱신: 2026-09-01

## 단계별 완료 여부

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 저장소 초기화 (git/.gitignore/.env.example) | 완료 (로컬 git init만, GitHub 원격 저장소는 운영자가 직접 생성 필요) |
| 1 | 개발 환경 준비 (venv, requirements.txt) | 완료 |
| 2 | 뉴스 수집 스크립트 (collect_news.py, keywords.json) | 코드 완료, RSS 수집 스모크 테스트 통과. 실제 Supabase 저장은 미검증(계정 없음) |
| 3 | 정제 로직 (clean_news.py) | 코드 완료, 실DB 검증 미완료(계정 없음) |
| 4 | 텔레그램 승인 시스템 (telegram_client.py, send_candidates.py, webhook/) | 코드 완료, 배포/실계정 테스트 미완료 |
| 5 | 프롬프트 변환 (generate_prompt.py) | 코드 완료, 실API 호출 미검증 |
| 6 | 이미지 생성 (generate_image.py) | 코드 완료, 실API 호출 미검증 |
| 7 | 이미지 검증 (verify_image.py) | 코드 완료, 실API 호출 미검증 |
| 8 | 카드 합성 (compose_card.py) | 코드 완료, **로컬 더미 이미지로 렌더링 테스트 통과**(제목 축약/줄바꿈/한글 폰트 정상 확인) |

전체 실행 편의를 위해 `run_daily.py collect` / `run_daily.py publish` 오케스트레이터 추가 (0~8단계 코드 자체는 모두 작성 완료, 다음 병목은 전부 "운영자의 외부 서비스 가입/키 발급").

## 외부 서비스 계정/키 발급 상태

값 자체는 절대 여기 기록하지 않고 `.env`에만 저장한다. 아래는 "발급 완료" 여부만 표시.

| 서비스 | 계정 생성 | API 키/토큰 발급 | 비고 |
|---|---|---|---|
| Telegram Bot (BotFather) | 미완료 | 미완료 | 운영자가 직접 가입/생성 필요 |
| Supabase | 미완료 | 미완료 | 운영자가 직접 가입 필요, 스키마는 `supabase_schema.sql`에 준비됨 |
| Vercel | 미완료 | 미완료 | 웹훅 배포용, 운영자가 직접 가입 필요. 배포 대상: `webhook/` 디렉터리 |
| Cloudflare (Workers AI) | 완료 | 완료 | FLUX 이미지 생성 + Gemma Vision 검증 + 프롬프트 변환 텍스트 모델까지 이 계정 하나로 공용 |
| ~~GLM API (Zhipu/Z.AI)~~ | 사용 안 함 | - | 무료 티어가 결제수단 등록을 요구해 **Cloudflare Workers AI 텍스트 모델로 대체**(아래 "설계 결정" 참고). 스펙의 "GLM-4.7-Flash 또는 상응 모델" 허용 문구에 따름 |
| GitHub (원격 저장소) | 미완료 | - | `gh` CLI 미설치 → 로컬 git init만 진행, 원격 저장소는 운영자가 브라우저에서 직접 생성 후 연결 필요 |

## 확인한 무료 티어 한도 (2026-09 기준, 웹 검색으로 확인 — 실제 계정 생성 후 대시보드에서 재확인 필요)

### Cloudflare Workers AI
- 매일 **10,000 뉴런(Neurons)** 무료 (Workers Free 플랜, 매일 00:00 UTC 리셋). 초과분은 1,000 뉴런당 $0.011.
- FLUX schnell(4-step) 이미지 1장 ≈ $0.0053 상당 → 뉴런 환산 시 이미지 1장당 약 480뉴런 내외로 추정 → 하루 10,000뉴런이면 이론상 FLUX 이미지 약 20장 여유 (하루 목표 3장 + 재시도 1회 = 최대 6장이므로 충분한 여유).
- Gemma Vision 검증(3~12b) 1회 호출당 정확한 뉴런 소모량은 검색으로 확인 안 됨 → **가정으로 취급**, 계정 생성 후 실측 필요.
- 소스: [Cloudflare Workers AI Free Tier 2026](https://pricepertoken.com/endpoints/cloudflare/free), [Cloudflare 공식 Pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)

### GLM API (Zhipu / Z.AI) — 사용 중단
- 웹 검색으로는 GLM-4.5-Flash/4.7-Flash가 $0 무료라는 정보가 나왔으나, **실제 가입 시 결제수단 등록을 요구**하는 것으로 확인됨(2026-09-01, 운영자 실제 가입 시도 결과). 무료라고 광고되는 티어라도 국가/계정 상태에 따라 결제수단 바인딩을 요구하는 경우가 흔함 — 실측이 웹 검색 결과보다 우선.
- 결제 없이 진행하기 위해 **Cloudflare Workers AI 텍스트 모델(`@cf/meta/llama-3.1-8b-instruct`)로 대체**함. 이미 FLUX/Gemma Vision에 쓰는 계정·토큰을 그대로 재사용하므로 추가 가입/결제 불필요. 스펙에 "GLM-4.7-Flash 또는 상응 모델"이라는 대체 허용 문구가 있어 범위 내 변경으로 판단.
- 관련 코드 변경: `generate_prompt.py`의 `call_glm()` → `call_llm()`으로 교체, `config.py`에서 `GLM_*` 변수 제거하고 `CLOUDFLARE_TEXT_MODEL` 추가.

### Supabase
- 무료 플랜: DB 500MB, egress 5GB, 캐시 egress 5GB, 파일 스토리지 1GB, edge function 호출 50만회/월, 활성 프로젝트 2개까지.
- **주의**: 무료 프로젝트는 7일간 활동이 없으면 자동 일시정지(pause)됨 → 매일 실행되는 스케줄이 있으므로 실제로는 문제 없을 것으로 예상되나, 스케줄이 중단되는 기간이 생기면 수동으로 재개(resume) 필요.
- 소스: [Supabase Pricing 2026](https://uibakery.io/blog/supabase-pricing), [Supabase Free Tier Limits 2026](https://automationatlas.io/answers/supabase-free-tier-limits-2026/)

### Vercel (Hobby 플랜)
- 서버리스 함수 호출 100만회/월, 함수 CPU 4시간/월, 함수 메모리 360GB-시간/월, Fast Data Transfer 100GB/월.
- 서버리스 함수 **최대 실행시간 60초** — 텔레그램 웹훅 응답은 즉시 반환하는 구조로 설계해야 함(무거운 작업은 웹훅 안에서 하지 않고 Supabase 업데이트만 하도록 함). `webhook/api/index.py`가 이 원칙대로 구현됨(승인/거절 처리만, 이미지 생성 등 무거운 작업 없음).
- Hobby 플랜은 개인/비상업적 용도로 제한됨 — 인스타 계정 운영이 상업적으로 커지면 Pro 전환 검토 필요(확장판 이슈로 기록).
- 소스: [Vercel Free Tier Limits 2026](https://deploywise.dev/blog/vercel-free-tier-limits-2026)

### Telegram Bot API
- 무료, 별도 유의미한 사용량 상한 없음 (일반적인 폴링/웹훅 사용 기준).

**결론**: 하루 3장(FLUX 3회 + 텍스트 프롬프트 변환 3회 + Gemma Vision 3~6회) 규모는 Cloudflare/Supabase/Vercel/Telegram 무료 한도 안에 여유 있게 들어올 것으로 판단됨(GLM은 결제 요구로 제외, Cloudflare 계정 하나로 통합). 단, Gemma Vision 및 텍스트 모델의 정확한 뉴런 소모량은 실측 전까지 가정으로 취급.

## 카테고리 분류 로직 판단 기준

- 수집 단계(`collect_news.py`)에서는 검색어(keyword) 자체가 속한 카테고리를 그대로 `category` 컬럼에 채우는 방식 채택 (기사 본문을 따로 분석해 재분류하지 않음). 이유: Google News RSS 검색 결과가 대체로 검색어 주제와 일치하고, MVP 단계에서 별도 분류 모델을 두는 것은 과설계로 판단.
- 검색어 자체가 카테고리 경계에 걸치는 경우(예: "반도체" 관련 개별 기업 실적 뉴스가 policy_industry 키워드로 잡히는 경우)는 완료 판단 기준의 "샘플 10건 수동 검토, 정확도 80%" 단계에서 실측 후 필요시 `keywords.json` 재조정으로 대응.

## 정제 로직(clean_news.py) 관련 설계 결정

- 원 스펙의 "본문 요약 최소 글자수" 필터를 위해 `news_items` 테이블에 `summary` 컬럼을 추가함(원 DDL에는 없던 컬럼, `supabase_schema.sql`에 반영).
- **한계**: Google News RSS의 `summary` 필드는 실제 기사 본문 요약이 아니라 "제목 + 언론사명"을 재구성한 짧은 문자열임(RSS 자체의 한계, 원문 스크래핑을 하지 않는 한 개선 불가). 따라서 `MIN_LENGTH` 필터는 사실상 "제목이 지나치게 짧은 저품질 기사"를 걸러내는 용도에 가깝다. 필요시 향후 원문 URL을 별도로 스크래핑해 실제 본문 길이를 확보하는 방향으로 확장 가능(확장판 이슈로 기록 가능).
- 중복 제거는 카테고리 내에서만 수행(그리디 클러스터링, `rapidfuzz.fuzz.token_sort_ratio` 기준 `DEDUP_SIMILARITY_THRESHOLD`=85 기본값). 대표 기사는 그룹 내 `published_at`이 가장 이른 기사로 선정.

## 텔레그램 승인 시스템 관련 설계 결정

- 웹훅(`webhook/api/index.py`)은 Vercel 서버리스 함수의 60초 제한과 콜드스타트를 고려해, 루트 프로젝트의 `db.py`(supabase-py) 대신 Supabase REST API를 `httpx`로 직접 호출하는 완전히 독립된 모듈로 작성함. 배포 시 `webhook/` 디렉터리를 Vercel 프로젝트 루트로 지정.
- "카테고리당 이미 승인된 건이 있으면 안내만" 로직은 `news_items.collected_at`이 한국시간 기준 오늘 자정 이후이고 `status='approved'`인 항목이 해당 카테고리에 있는지로 판단(별도의 "오늘 배치" 개념 컬럼이 없어 `collected_at`을 대리 지표로 사용).

## 프롬프트 변환 관련 설계 결정 (GLM → Cloudflare 전환)

- `generate_prompt.py`는 더 이상 GLM을 호출하지 않고, `config.CLOUDFLARE_TEXT_MODEL`
  (기본값 `@cf/meta/llama-3.1-8b-instruct`)로 같은 Cloudflare Workers AI 계정에
  텍스트 생성을 요청한다. FLUX/Gemma Vision과 동일한 `CLOUDFLARE_ACCOUNT_ID`/
  `CLOUDFLARE_API_TOKEN`을 재사용하므로 별도 키 관리가 필요 없다.
- `verify_image.py`의 재시도 경로(`call_llm`)도 동일하게 갱신됨.

## 이미지 생성/검증/합성 관련 설계 결정

- Gemma Vision 호출 포맷은 Cloudflare의 최신 멀티모달 chat `messages` + `image_url`(base64 data URL) 형식으로 작성함. **미검증** — 실제 계정 생성 후 Cloudflare 문서/응답으로 정확한 요청 스키마 재확인 필요(모델별로 입력 포맷이 다를 수 있음).
- 카드 합성(`compose_card.py`)은 Windows 기본 한글 폰트(맑은 고딕, `C:/Windows/Fonts/malgun.ttf`)를 기본값으로 사용. 다른 OS/서버에 배포 시 `.env`의 `FONT_PATH`/`FONT_BOLD_PATH`를 실제 폰트 경로로 재설정해야 함.
- 제목 축약(`truncate_title`)과 카드 렌더링(`compose`)은 **더미 배경 이미지로 로컬 테스트 완료** — 한글 텍스트 깨짐 없이 정상 렌더링, 30자 제한 준수 확인.

## 남은 이슈 / TODO

1. 운영자가 아래 서비스에 직접 가입하고 `.env`에 키를 채워야 end-to-end 실행 가능 (Telegram/Supabase/Vercel/Cloudflare 완료, GLM은 결제 요구로 사용 중단):
   - Telegram Bot (BotFather에서 `/newbot`) — 완료
   - Supabase (프로젝트 생성 후 `supabase_schema.sql` 실행) — 완료
   - Vercel (`webhook/` 디렉터리 배포, 환경변수 설정 후 `python set_telegram_webhook.py <배포URL>/webhook` 실행) — 완료
   - Cloudflare (Workers AI 활성화) — 완료
2. GitHub 원격 저장소: `gh` CLI가 이 환경에 설치돼 있지 않아 로컬 `git init`만 진행함. 운영자가 GitHub 웹에서 저장소(`econ-stock-cardnews-bot`, private 추천)를 만들고 아래 명령으로 연결해야 함:
   ```bash
   git remote add origin <저장소 URL>
   git push -u origin master
   ```
3. 외부 계정이 모두 갖춰졌으므로 `python run_daily.py collect` / `publish`로 실제 end-to-end 테스트 진행 필요. 현재까지는 RSS 수집 스모크 테스트와 카드 합성(더미 이미지) 렌더링 테스트만 완료됨.
4. Gemma Vision의 정확한 요청 스키마 및 실제 뉴런 소모량은 계정 생성 후 재확인 필요.
5. 완료 판단 기준의 "카테고리 자동 분류 샘플 10건 수동 검토", "동일 사건 기사 2건 dedup 그룹핑 확인", "텔레그램 승인 end-to-end 확인" 등은 모두 실제 계정 발급 이후 진행 가능.

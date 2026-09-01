# 진행 상태

마지막 갱신: 2026-09-01

## 단계별 완료 여부

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 저장소 초기화 (git/.gitignore/.env.example) | 진행 중 |
| 1 | 개발 환경 준비 (venv, requirements.txt) | 완료 |
| 2 | 뉴스 수집 스크립트 (collect_news.py, keywords.json) | 완료 (코드), 실DB 저장 미검증 (Supabase 미가입) |
| 3 | 정제 로직 (clean_news.py) | 완료 (코드), 실DB 검증 미완료 |
| 4 | 텔레그램 승인 시스템 | 미착수 |
| 5 | 프롬프트 변환 (generate_prompt.py) | 미착수 |
| 6 | 이미지 생성 (generate_image.py) | 미착수 |
| 7 | 이미지 검증 (verify_image.py) | 미착수 |
| 8 | 카드 합성 (compose_card.py) | 미착수 |

## 외부 서비스 계정/키 발급 상태

값 자체는 절대 여기 기록하지 않고 `.env`에만 저장한다. 아래는 "발급 완료" 여부만 표시.

| 서비스 | 계정 생성 | API 키/토큰 발급 | 비고 |
|---|---|---|---|
| Telegram Bot (BotFather) | 미완료 | 미완료 | 운영자가 직접 가입/생성 필요 |
| Supabase | 미완료 | 미완료 | 운영자가 직접 가입 필요, 스키마는 `supabase_schema.sql`에 준비됨 |
| Vercel | 미완료 | 미완료 | 웹훅 배포용, 운영자가 직접 가입 필요 |
| Cloudflare (Workers AI) | 미완료 | 미완료 | 운영자가 직접 가입 필요 |
| GLM API (Zhipu/Z.AI) | 미완료 | 미완료 | 운영자가 직접 가입 필요 |
| GitHub (원격 저장소) | 미완료 | - | `gh` CLI 미설치 → 로컬 git init만 진행, 원격 저장소는 운영자가 브라우저에서 직접 생성 후 `git remote add origin <url>` 필요 |

## 확인한 무료 티어 한도 (2026-09 기준, 웹 검색으로 확인 — 실제 계정 생성 후 대시보드에서 재확인 필요)

### Cloudflare Workers AI
- 매일 **10,000 뉴런(Neurons)** 무료 (Workers Free 플랜, 매일 00:00 UTC 리셋). 초과분은 1,000 뉴런당 $0.011.
- FLUX schnell(4-step) 이미지 1장 ≈ $0.0053 상당 → 뉴런 환산 시 이미지 1장당 약 480뉴런 내외로 추정 → 하루 10,000뉴런이면 이론상 FLUX 이미지 약 20장 여유 (하루 목표 3장 + 재시도 1회 = 최대 6장이므로 충분한 여유).
- Gemma Vision 검증(3~12b) 1회 호출당 정확한 뉴런 소모량은 검색으로 확인 안 됨 → **가정으로 취급**, 계정 생성 후 실측 필요.
- 소스: [Cloudflare Workers AI Free Tier 2026](https://pricepertoken.com/endpoints/cloudflare/free), [Cloudflare 공식 Pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)

### GLM API (Zhipu / Z.AI)
- GLM-4.5-Flash / GLM-4.7-Flash는 입력·출력·캐시 입력 모두 **$0 (무료)** 로 제공.
- 다만 무료 모델은 별도로 **속도 제한(rate limit)** 이 걸려 있음 — 정확한 RPM/TPM 수치는 공개 검색으로 확인 불가, **z.ai 대시보드에서 가입 후 직접 확인 필요**로 가정 처리.
- GLM-4.5-Flash는 조만간 GLM-4.7-Flash로 자동 라우팅될 예정이라는 공지가 있었음 → `.env`의 `GLM_MODEL`은 설정 가능하게 분리해둠(`config.py` 참고).
- 소스: [Z.AI GLM API Pricing 2026](https://developer.puter.com/tutorials/zai-glm-api-pricing/), [Z.AI 공식 문서](https://docs.z.ai/guides/llm/glm-4.5)

### Supabase
- 무료 플랜: DB 500MB, egress 5GB, 캐시 egress 5GB, 파일 스토리지 1GB, edge function 호출 50만회/월, 활성 프로젝트 2개까지.
- **주의**: 무료 프로젝트는 7일간 활동이 없으면 자동 일시정지(pause)됨 → 매일 실행되는 스케줄이 있으므로 실제로는 문제 없을 것으로 예상되나, 스케줄이 중단되는 기간이 생기면 수동으로 재개(resume) 필요.
- 소스: [Supabase Pricing 2026](https://uibakery.io/blog/supabase-pricing), [Supabase Free Tier Limits 2026](https://automationatlas.io/answers/supabase-free-tier-limits-2026/)

### Vercel (Hobby 플랜)
- 서버리스 함수 호출 100만회/월, 함수 CPU 4시간/월, 함수 메모리 360GB-시간/월, Fast Data Transfer 100GB/월.
- 서버리스 함수 **최대 실행시간 60초** — 텔레그램 웹훅 응답은 즉시 반환하는 구조로 설계해야 함(무거운 작업은 웹훅 안에서 하지 않고 Supabase 업데이트만 하도록 함).
- Hobby 플랜은 개인/비상업적 용도로 제한됨 — 인스타 계정 운영이 상업적으로 커지면 Pro 전환 검토 필요(확장판 이슈로 기록).
- 소스: [Vercel Free Tier Limits 2026](https://deploywise.dev/blog/vercel-free-tier-limits-2026)

### Telegram Bot API
- 무료, 별도 유의미한 사용량 상한 없음 (일반적인 폴링/웹훅 사용 기준).

**결론**: 하루 3장(FLUX 3회 + Gemma Vision 3~6회) 규모는 Cloudflare/GLM/Supabase/Vercel/Telegram 무료 한도 안에 여유 있게 들어올 것으로 판단됨. 단, GLM 무료 티어의 정확한 RPM 제한과 Gemma Vision의 뉴런 소모량은 실측 전까지 가정으로 취급.

## 카테고리 분류 로직 판단 기준

- 수집 단계(`collect_news.py`)에서는 검색어(keyword) 자체가 속한 카테고리를 그대로 `category` 컬럼에 채우는 방식 채택 (기사 본문을 따로 분석해 재분류하지 않음). 이유: Google News RSS 검색 결과가 대체로 검색어 주제와 일치하고, MVP 단계에서 별도 분류 모델을 두는 것은 과설계로 판단.
- 검색어 자체가 카테고리 경계에 걸치는 경우(예: "반도체" 관련 개별 기업 실적 뉴스가 policy_industry 키워드로 잡히는 경우)는 완료 판단 기준의 "샘플 10건 수동 검토, 정확도 80%" 단계에서 실측 후 필요시 `keywords.json` 재조정으로 대응.

## 정제 로직(clean_news.py) 관련 설계 결정

- 원 스펙의 "본문 요약 최소 글자수" 필터를 위해 `news_items` 테이블에 `summary` 컬럼을 추가함(원 DDL에는 없던 컬럼, `supabase_schema.sql`에 반영).
- **한계**: Google News RSS의 `summary` 필드는 실제 기사 본문 요약이 아니라 "제목 + 언론사명"을 재구성한 짧은 문자열임(RSS 자체의 한계, 원문 스크래핑을 하지 않는 한 개선 불가). 따라서 `MIN_LENGTH` 필터는 사실상 "제목이 지나치게 짧은 저품질 기사"를 걸러내는 용도에 가깝다. 필요시 향후 원문 URL을 별도로 스크래핑해 실제 본문 길이를 확보하는 방향으로 확장 가능(확장판 이슈로 기록 가능).
- 중복 제거는 카테고리 내에서만 수행(그리디 클러스터링, `rapidfuzz.fuzz.token_sort_ratio` 기준 `DEDUP_SIMILARITY_THRESHOLD`=85 기본값). 대표 기사는 그룹 내 `published_at`이 가장 이른 기사로 선정.

## 남은 이슈 / TODO

1. 운영자가 아래 5개 서비스에 직접 가입하고 `.env`에 키를 채워야 다음 단계(4~8) 진행 가능:
   - Telegram Bot (BotFather에서 `/newbot`)
   - Supabase (프로젝트 생성 후 `supabase_schema.sql` 실행)
   - Vercel (웹훅 배포용)
   - Cloudflare (Workers AI 활성화)
   - GLM API (z.ai 가입 후 API 키 발급)
2. GitHub 원격 저장소: `gh` CLI가 이 환경에 설치돼 있지 않아 로컬 `git init`만 진행함. 운영자가 GitHub 웹에서 저장소(`econ-stock-cardnews-bot`, private 추천)를 만들고 아래 명령으로 연결해야 함:
   ```bash
   git remote add origin <저장소 URL>
   git push -u origin main
   ```
3. `collect_news.py`, `clean_news.py`는 실제 Supabase 프로젝트가 없어 RSS 수집 자체만 스모크 테스트 완료(정상 동작 확인), DB 저장/정제 파이프라인 end-to-end는 미검증.
4. Gemma Vision의 실제 뉴런 소모량 등 Cloudflare 세부 비용은 계정 생성 후 재확인 필요.

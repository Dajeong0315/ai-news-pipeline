# 디징(youth) 수집 inbox

2026-10-30부터 디징은 뉴스 기사 대신 "지금 신청할 수 있는 공식 모집 공고"를 원본으로 쓴다.
이 폴더는 공고를 모아두는 곳이고, 파이프라인(Claude Code가 만드는 수집 단계)이 읽어 선정·카드 작성에 쓴다.

## 누가 채우나

| 출처 | 방식 | 카테고리 |
|-|-|-|
| 위비티·링커리어 등 대외활동·공모전 사이트 | Aside 아침 루틴(매일 07:00 KST, 브라우저) → `aside-YYYY-MM-DD.json` | youth_activity |
| 온통청년 청년정책 | 파이프라인 API 수집(`https://www.youthcenter.go.kr/go/ythip/getPlcy?apiKeyNm=...&rtnType=json`, 키는 youth/.env `YOUTHCENTER_API_KEY`, 2027-07-28까지). Aside 루틴은 보지 않음 | youth_support |
| K-Startup 창업 지원사업 공고 | 파이프라인 API 수집(공공데이터포털 승인됨, 같은 `DATA_GO_KR_SERVICE_KEY`) | youth_support |
| 잡알리오 공공기관 채용(청년인턴·신입) | 파이프라인 API 수집(공공데이터포털 `apis.data.go.kr/1051000/recruitment/list`, 키는 youth/.env `DATA_GO_KR_SERVICE_KEY`) | youth_job |

## 파일 형식 (`aside-YYYY-MM-DD.json`, UTF-8)

```json
{
  "collected_at": "2026-10-31T07:12:00+09:00",
  "source": "aside-routine",
  "items": [
    {
      "id": "wevity:111899",
      "category": "youth_activity",
      "kind": "공모전",
      "title": "공고 제목(원문 그대로)",
      "host": "주최/주관",
      "target": "응모 대상(원문 표현)",
      "benefit": "혜택·상금·활동비(원문 표현, 없으면 null)",
      "apply_start": "2026-10-20",
      "apply_end": "2026-11-20",
      "status": "접수중",
      "region": "전국",
      "fee": "무료",
      "official_url": "주최 공식 공고·신청 페이지(없으면 null)",
      "listing_url": "수집한 목록 사이트 상세 URL",
      "raw_text": "상세 내용 원문(최대 4000자, 요약하지 않음)",
      "checks": {"official_page_opened": true, "dates_match_official": true, "notes": "확인 메모"}
    }
  ]
}
```

- 날짜는 `YYYY-MM-DD`. 원문에 없으면 null(지어내지 않음).
- `status`: "접수중" 또는 "접수예정".
- 같은 `listing_url`이 이전 7일 inbox에 있으면 다시 넣지 않는다(마감·내용이 바뀐 경우만 다시 넣고 notes에 적음).

- `-test`가 붙은 파일(예: `aside-2026-10-30-test.json`)은 시험 실행 결과다. 파이프라인은 읽지 않는다.
- 추가 칸(선택): `onsite`(현장 방문·촬영이 필수면 장소 문자열, 아니면 null), `self_pay`(정부 바우처처럼 본인부담금이 있으면 원문 표현, 없으면 null).

## 넣는 기준

- 마감(`apply_end`)이 오늘+3일과 같거나 그 뒤(오늘이 10/30이면 11/2 마감까지 포함). 접수예정이면 시작이 7일 이내.
- 마감이 "상시"·"예산 소진 시"라 날짜가 없는 것은 apply_end=null로 넣되, 전국 단위 정부 사업만(카드감은 파이프라인 선정에서 낮게 본다).
- 2030이 신청 가능(대학생·청년·일반인·만 19~34세 등). 청소년·어린이 전용, 특정 학교 재학생 전용, 시군구 주민 전용은 제외.
- 무료 참여. 참가비·수강료·결제·할인코드·출판비 등 돈을 내는 프로그램, 업체 서비스 홍보성 "공모전", 특정 매장 방문 홍보(체험단·방문형 블로그)는 제외.
- 정부·공공 바우처처럼 소득별 본인부담금만 있는 지원사업은 포함하고 `self_pay`에 적는다(참가비와 다름).
- 같은 공고가 여러 제목으로 올라온 경우 하나만.
- 주최가 확인되는 곳(정부·공공기관·지자체 광역·기업·재단·언론). 개인·불명 주최 제외.
- 지역: 전국 우선. 광역 시도 단위(서울·부산 등)는 region에 적고 포함 가능, 시군구 단위 제외.

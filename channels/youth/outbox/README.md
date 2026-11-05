# 디징(youth) outbox: 스토리 공유 대기 목록 · 댓글 자료 목록

파일 두 개: `stories.json`(스토리 공유, 아래) · `guides.json`(댓글 → 자료 DM, 맨 아래). 둘 다 git에 안 올린다.

2026-11-01부터 디징 캐러셀을 스토리로 "다시 공유"하는 일은 Aside 브라우저 루틴이 맡는다.
파이프라인의 헤드리스 브라우저(share_to_story.py)는 Meta가 로그인을 풀어 버려서 쓰지 않는다(channel.json `share_post_to_story: false`).

## 흐름

1. 파이프라인(`publish_instagram.py`, 12:00·19:00)이 캐러셀을 게시하면 `stories.json`에 항목을 하나 넣는다(channel.json `story_queue: true`).
2. Aside 루틴(12:30·19:30)이 `shared_at`이 비어 있는 항목을 Business Suite에서 공유하고 `shared_at`을 채운다.
3. 다음 게시 실행 때 파이프라인(`story_queue.check`)이 Graph API `/stories`로 실제 스토리를 찾아 `story_id`를 적는다.
   게시 2시간 뒤에도 `shared_at`이 비었거나, 공유 30분 뒤에도 스토리가 안 보이면 텔레그램으로 한 번 알린다.

## 파일 형식 (`stories.json`, UTF-8)

```json
{
  "items": [
    {
      "media_id": "18360383050264625",
      "permalink": "https://www.instagram.com/p/DfL4CK3ErbD/",
      "title": "카드 제목",
      "card_id": 62,
      "published_at": "2026-11-01T13:30:04+09:00",
      "shared_at": null,
      "shared_by": null,
      "notes": null,
      "story_id": null,
      "verified_at": null,
      "alerted": null
    }
  ]
}
```

| 필드 | 누가 쓰나 | 뜻 |
|-|-|-|
| media_id·permalink·title·card_id·published_at | 파이프라인 | 게시한 캐러셀(릴스 아님) |
| shared_at | Aside(또는 손으로 공유한 운영자) | "스토리에 추가"를 누른 시각, KST ISO(예 `2026-11-01T12:34:10+09:00`). 공유 못 했으면 null 그대로 |
| shared_by | Aside | `"aside-routine"`(손으로 했으면 `"operator"`) |
| notes | Aside | 공유 못 한 이유·특이사항(한 줄) |
| story_id·verified_at·alerted | 파이프라인 | Graph API 확인 결과·알림 여부. Aside는 건드리지 않는다 |

- Aside는 `shared_at`·`shared_by`·`notes`만 바꾸고 다른 항목·필드는 그대로 둔다. 파일을 통째로 다시 쓸 때도 읽은 내용을 보존한다.
- 14일 지난 항목은 파이프라인이 지운다.

## Aside 루틴 지시문(12:30·19:30, 같은 내용)

```text
인스타 카드뉴스 계정 "디징"(@dijing.kr)에 오늘 게시된 캐러셀을 인스타 스토리로 "다시 공유"한다. 결과는 한국어 반말로 짧게 보고(이모지·em dash 금지).

## 배경
- 프로젝트 폴더: H:\내 드라이브\경제·주식 카드뉴스 파이프라인 인수인계\운영후보
- channels\youth\outbox\README.md 를 먼저 읽고 그대로 따른다. 대기 목록: channels\youth\outbox\stories.json
- 이 환경은 Bash로 프로젝트 폴더를 못 쓴다. 파일은 REPL의 fs(또는 write_file)로 쓰고, 쓴 뒤 다시 읽어 JSON.parse로 확인.
- 파일이 없거나 공유할 항목이 없으면 아무것도 열지 말고 "공유할 게시물 없음"으로 보고하고 끝낸다.

## 1. 대상 고르기
- stories.json 의 items 중 shared_at 이 null 이고 published_at 이 지금부터 24시간 이내인 항목만. 24시간 넘은 미공유 항목은 공유하지 말고 보고에만 적는다.
- 한 번 실행에 최대 3개.

## 2. 공유 (항목마다)
1) https://business.facebook.com/latest/posts/published_posts?asset_id=1334955663042764 을 연다. 4~5초 기다린다.
   안내 팝업("완료", "닫기", "나중에")이 있으면 닫는다. 로그인 화면이 나오면 멈추고 보고한다(로그인 시도 금지).
2) 목록 위 "ID 또는 캡션으로 검색" 칸에 media_id 를 그대로 넣고 Enter. 4~5초 기다린다.
3) 남은 줄이 정확히 1개이고, 그 줄 유형이 "슬라이드"(릴스 아님)인지 확인한다. 아니면 공유하지 말고 notes에 적는다.
4) 그 줄의 "⋯" 버튼 → "스토리에 다시 공유" → 대화상자 "Instagram 스토리에 다시 공유"에서 "스토리에 추가"를 누른다.
   "미디어 수정"은 누르지 않는다. 대화상자가 안 뜨거나 버튼이 없으면 notes에 적고 다음 항목으로.
5) "스토리에 추가"를 누르고 대화상자가 닫히면, 그 시각을 shared_at 에 KST ISO(예 2026-11-01T12:34:10+09:00)로, shared_by 에 "aside-routine" 을 적는다.
- 같은 게시물을 두 번 공유하지 않는다. 누른 뒤 결과가 애매하면 다시 누르지 말고 스토리 탭(왼쪽 "스토리")에서 방금 스토리가 있는지만 보고 notes에 적는다.
- 페이지 이동 사이 2~3초 쉰다. 다른 게시물·설정·광고("홍보하기")는 건드리지 않는다.

## 3. 저장
- stories.json 을 읽어 해당 항목의 shared_at·shared_by·notes 만 바꾸고 나머지 항목·필드는 그대로 둔 채 저장한다.
- 저장 후 다시 읽어 JSON.parse, 바꾼 항목의 shared_at 이 들어갔는지 코드로 확인.

## 4. 보고 (notify_parent)
- 공유한 게시물 제목·media_id·shared_at, 공유 못 한 항목과 이유, 24시간 넘어 건너뛴 미공유 항목.
- 금지: 새 게시물 올리기, 홍보하기·광고, 댓글·좋아요, 로그인 시도, 파이프라인 실행, 프로젝트 코드·설정·.env 수정(outbox\stories.json 쓰기만 허용).
- 끝나면 연 탭은 닫는다.
```

## 댓글 → 자료 DM: `guides.json` (2026-11-02)

DM 발송은 리틀리가 한다. 파이프라인은 캡션 안내 문구와 이 자료 목록만 만들고, 노션 가이드 페이지·리틀리 설정은 Aside 루틴이 한다.

- 설정: channel.json `comment_dm` = `keyword`(디징 "가이드", 경제 "자료")·`caption_line`·`hub_url`(노션 허브)·`enabled`(빠지면 켜짐).
  묘한경제는 리틀리 연동 전이라 `enabled: false` → 캡션 문구도, guides.json 기록도 안 한다.
- 캡션: 켜진 채널이면 `caption_line`을 캡션 본문 마지막 친구 태그 줄 바로 앞에 1줄(태그 줄이 없으면 본문 마지막 줄 앞).
  2200자 검사에 포함된다. 카드 생성 때 넣고, 못 넣었으면 게시 직전에 넣는다(넣으면 2200자를 넘을 때는 뺀다).
- 게시 성공 시 파이프라인(`comment_dm.add_guide`)이 `items`에 항목을 하나 넣는다(같은 media_id는 한 번만). 캐러셀 기준, 릴스는 따로 안 넣는다.

```json
{
  "items": [
    {
      "media_id": "18360383050264625",
      "permalink": "https://www.instagram.com/p/DfL4CK3ErbD/",
      "title": "2026년도 하반기 체험형 청년인턴 채용 공고",
      "host": "한국도로교통공단",
      "target": "신입 · 청년인턴(체험형)",
      "benefit": "청년인턴(체험형) 116명 채용",
      "apply_start": "2026-10-26",
      "apply_end": "2026-11-09T18:00:00+09:00",
      "how_to_apply": "온라인 원서접수 https://koroad.saramin.co.kr (주최 한국도로교통공단)",
      "documents": null,
      "cautions": "사회형평·장애·의사상자는 가점·별도 전형이니 서류 확인 필수",
      "official_url": "https://www.koroad.or.kr/main/recruit/recruitment_list.do",
      "caption": "게시한 캡션 전문",
      "published_at": "2026-11-02T12:10:31+09:00",
      "notion_url": null,
      "notion_page_id": null,
      "littly_set": null,
      "guide_done_at": null
    }
  ]
}
```

| 필드 | 누가 쓰나 | 뜻 |
|-|-|-|
| media_id·permalink | 파이프라인 | 게시한 캐러셀 ID·링크(링크를 못 받으면 null) |
| title | 파이프라인 | 카드 제목(공고명) |
| host | 파이프라인 | 주최·기관(공고 출처 기관) |
| target·benefit | 파이프라인 | 대상·혜택. 공고 원문 값, 없으면 캡션 📌·💼 줄 |
| apply_start·apply_end | 파이프라인 | 접수 시작·마감. 원문에 그 날짜의 시각이 있으면 KST ISO(`2026-11-09T18:00:00+09:00`), 없으면 날짜만(`2026-10-26`). 상시·기사 카드는 null |
| how_to_apply·cautions | 파이프라인 | 캡션 ✅·⚠️ 줄 |
| documents | 파이프라인 | 제출 서류·준비물. 원문에 있을 때만(앞뒤 3줄 요약), 없으면 null |
| official_url | 파이프라인 | 공식 공고 링크(없으면 수집 출처 링크) |
| caption | 파이프라인 | 실제 게시한 캡션 전문 |
| published_at | 파이프라인 | 게시 시각 KST ISO |
| notion_url·notion_page_id | Aside | 노션 허브 아래 만든 가이드 페이지 링크·ID |
| littly_set | Aside | 리틀리 댓글 DM 설정을 마쳤으면 true(실패면 false, 아직이면 null) |
| guide_done_at | Aside | 노션 페이지·리틀리 설정을 다 끝낸 시각 KST ISO |

- Aside는 notion_url·notion_page_id·littly_set·guide_done_at만 바꾸고 다른 항목·필드는 그대로 둔다. 파일을 통째로 다시 쓸 때도 읽은 내용을 보존한다.
- 경제 채널은 `channels/economy/outbox/guides.json`(같은 형식, 기사 카드라 host·target·apply_* 등은 대개 null). 지금은 꺼져 있어 안 생긴다.

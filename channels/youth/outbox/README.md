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
- 프로젝트 폴더: H:\내 드라이브\경제·주식 카드뉴스 자동화 파이프라인
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

DM 발송은 리틀리가 한다. 파이프라인은 캡션 안내 문구와 이 자료 목록만 만들고, Aside가 노션 허브 아래 하위 페이지를 만든다.

- 캡션 안내 문구: channel.json `comment_dm`(디징 키워드 "가이드", 경제 "자료")의 `caption_line`을 캡션 본문 마지막 친구 태그 줄 바로 앞에 1줄.
  `hub_url`이 비어 있으면 넣지 않는다. Aside가 노션 허브를 만든 뒤 channel.json `comment_dm.hub_url`을 채우면 그다음 게시부터 들어간다
  (카드 생성 때 못 넣었으면 게시 직전에 넣는다, 2200자를 넘으면 뺀다).
- 게시 성공 시 파이프라인(`comment_dm.add_guide`)이 항목을 하나 넣는다. Aside는 `notion_url`만 채우고 다른 필드는 그대로 둔다.
- 경제 채널은 `channels/economy/outbox/guides.json`(같은 형식, 공고 필드는 대부분 null).

```json
{
  "items": [
    {
      "media_id": "18360383050264625",
      "permalink": "https://www.instagram.com/p/DfL4CK3ErbD/",
      "title": "카드 제목",
      "target": "대상(공고 원문, 없으면 캡션 📌 줄)",
      "benefit": "혜택(공고 원문, 없으면 캡션 💼 줄)",
      "period": "2026-10-27 ~ 2026-11-16",
      "how_to_apply": "신청 방법(캡션 ✅ 줄)",
      "materials": "준비물·제출 서류(원문에 있을 때만, 없으면 null)",
      "cautions": "주의(캡션 ⚠️ 줄)",
      "official_url": "공식 공고 링크",
      "published_at": "2026-11-02T12:10:31+09:00",
      "notion_url": null
    }
  ]
}
```

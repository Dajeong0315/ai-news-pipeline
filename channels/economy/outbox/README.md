# 묘한 경제(economy) outbox: 댓글 자료 목록

`guides.json`: 게시 성공 시 파이프라인이 항목을 넣고 Aside가 노션 하위 페이지(계산표 등)를 만든 뒤 `notion_url`을 채운다.
형식·규칙은 `channels/youth/outbox/README.md`의 "댓글 → 자료 DM"과 같다(경제는 기사 카드라 target·benefit·period 등은 대개 null).
캡션 안내 문구는 channel.json `comment_dm`(키워드 "자료")의 `hub_url`이 채워진 뒤부터 들어간다. json은 git에 안 올린다.

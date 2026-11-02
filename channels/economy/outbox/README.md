# 묘한 경제(economy) outbox: 댓글 자료 목록

`guides.json`: 게시 성공 시 파이프라인이 항목을 넣고, Aside가 노션 하위 페이지(계산표 등)·리틀리 설정을 한 뒤 notion_url·notion_page_id·littly_set·guide_done_at을 채운다.
형식·규칙은 `channels/youth/outbox/README.md`의 "댓글 → 자료 DM"과 같다(경제는 기사 카드라 host·target·apply_* 등은 대개 null).
지금은 리틀리 연동 전이라 channel.json `comment_dm.enabled: false` → 캡션 안내 문구도, guides.json 기록도 안 한다. 켜려면 그 값을 지우거나 true로. json은 git에 안 올린다.

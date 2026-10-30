# 주간 템플릿 리서치 (2026-10-30 시작)

매주 일요일 Aside 루틴이 인스타그램에서 반응 좋은 카드뉴스 게시물·릴스를 보고, 새 템플릿을 만들거나 기존 템플릿을 고쳐
다음 주(월요일부터) 순환에 넣는다. 이 폴더는 그 기록과 도구를 둔다.

## 파일

- `YYYY-MM-DD.md`: 그 주 리서치 보고서(참고한 게시물·반응 수치·뽑은 디자인 요소·만든/고친 템플릿·순환 변경)
- `log.md`: 주차별 한 줄 요약 + 이미 참고한 게시물 URL(중복 참고 방지)
- `preview_in_browser.js`: 파이썬·노드 없이 브라우저(Aside REPL)에서 템플릿을 렌더하고 render.js와 같은 QA를 돌리는 도구
- `sample-youth-deck.json`: 정보담당 미리보기용 샘플 덱(경제는 `output/template-sample/deck-preview.json`)

## 기준

- 반응 지표: 좋아요+댓글(게시물 페이지의 og:description), 릴스는 조회수가 보이면 같이. 팔로워 대비 비율로 비교.
  최근 30일 게시물 중 계정 평균의 2배 이상이거나 상위 3개를 "반응 좋은 게시물"로 본다.
- 가져오는 것은 레이아웃·구성·장치(말풍선, 체크리스트, 대화 도입, 표지 구도 등). 남의 로고·캐릭터·문구·사진은 가져오지 않는다.
- 채널 톤
  - 경제(묘한 경제): 사진형 + 고양이 마스코트 버전과 `:plain` 버전. 순환은 `config.py`의 `CARD_TEMPLATES` 기본값.
  - 청년(정보담당): 따뜻한 필름톤·크림/베이지·얇은 산스+명조 제목·작은 UI 소품. 캐릭터·이모지·원색 없음.
    단톡·공유·폴더·메모 같은 "친구가 챙겨주는" 장면. 순환은 `channels/youth/channel.json`의 `card_templates`.
- 새 템플릿 체크리스트(card_templates/README.md "새 템플릿 추가"와 같음)
  1. `card_templates/<이름>/template.html` (`../_shared/base.css`, `../_shared/engine.js` 사용, 위치 잡는 요소는 `deco`에)
  2. `card_templates/images.py`의 `STYLE_SUFFIX`에 `"<이름>": "<영어 배경 사진 스타일>"` 추가(없으면 카드 생성이 KeyError로 실패)
  3. props는 기존 키만 사용(script, closing_script, window_title, buttons, closing_buttons, memo_title, folders, label, ask)
  4. 샘플 덱으로 렌더 → QA errors 0개(청년은 strict) → `previews/<이름>.jpg`(표지)와 `previews/<이름>-sheet` 장들 저장
  5. 순환에 추가, `card_templates/README.md` 템플릿 표에 한 줄 추가, `PROGRESS.md`에 기록
- 순환은 채널당 최대 12개. 넘으면 반응이 가장 약했던 템플릿을 빼고 이유를 보고서에 적는다.

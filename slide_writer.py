"""기사 원문 → 카드뉴스 슬라이드 JSON (2026-10-29 내용 가공 재설계, 템플릿 시안용).

기존 generate_cardset.py는 표지·개념 비유·근거·의미·행동 제안·고지를 매번 같은
순서로 채우게 해서, 기사에 없는 내용으로 빈칸을 메우는 일이 잦았다(운영자 피드백:
"불필요한 정보가 질 낮게 들어 있다"). 여기서는:

- 슬라이드 수(채널 설정, 경제 3~7)와 종류(cover/stat/compare/point/list/quote/closing)를 기사에
  맞춰 모델이 고른다. 고정 페이지 없음. 한 장 = 메시지 하나.
- 숫자는 원문에 있는 것만 쓴다. 코드가 슬라이드 속 숫자를 원문과 대조해
  없으면 재생성한다(LLM=품질, 코드=제약 원칙).
- 투자 유의 문구는 슬라이드가 아니라 캡션 끝에 코드가 붙인다.
- 기사 제목의 한자 약어(美·中 등)는 모델에 넘기기 전에 한글로 푼다.

출력 형식은 card_templates/의 공용 슬라이드 스키마를 따른다.

실행(시안 확인용):
    python slide_writer.py output/template-sample/article.json output/template-sample/deck.json
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import httpx
from rapidfuzz import fuzz

import config
from llm_client import call_text_model

MAX_ATTEMPTS = 3
# 2026-10-29 비교: llama-3.3-70b는 같은 숫자 반복·기사체·표지 반복이 심해 gpt-oss-120b를 기본으로 둔다.
MODEL = os.environ.get("SLIDE_WRITER_MODEL", "@cf/openai/gpt-oss-120b")
DISCLAIMER = config.CHANNEL_CONFIG["disclaimer"]  # 채널별. 비어 있으면 붙이지 않음
# 채널별 장수·캡션 방식. 청년 채널은 카드 1~4장 + 자세한 캡션(2026-10-30 운영자), 경제는 기존 3~7장 + 짧은 캡션.
SLIDES_MIN, SLIDES_MAX = config.CHANNEL_CONFIG.get("slide_range", [3, 7])
CAPTION_SPEC = config.CHANNEL_CONFIG.get("caption_spec", "인스타 캡션 본문(3~5문장, 해시태그 제외)")
CHECK_CAPTION = config.CHANNEL_CONFIG.get("check_caption", False)  # 캡션에 사실을 많이 담는 채널은 캡션 숫자도 원문과 대조
CAPTION_MAX = config.CHANNEL_CONFIG.get("caption_max", 2000)

HANJA_ABBREVIATIONS = {
    "美": "미국", "中": "중국", "日": "일본", "韓": "한국", "北": "북한", "英": "영국",
    "獨": "독일", "佛": "프랑스", "露": "러시아", "印": "인도", "豪": "호주", "臺": "대만",
    "台": "대만", "歐": "유럽", "亞": "아시아", "與": "여당", "野": "야당", "前": "전", "現": "현",
}
_STRAY_SCRIPT_RE = re.compile(r"[぀-ヿ㐀-鿿]")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_BOILERPLATE_RE = re.compile(r"<?ⓒ.*$|무단전재.*$", re.MULTILINE)
# 운영자가 질 낮다고 지적한 뻔한 마무리·투자 암시 표현(2026-10-29)
BANNED_PHRASES = ("오를 가능성", "염두에", "기대해 볼", "주목해 보세요", "기억해 주세요", "핵심을 기억",
                  "공부해 보", "관심을 가져", "투자에 유의", "알아두면 좋",
                  # 번역투·기사체·과장(가독성 피드백, 2026-10-29)
                  "것으로 나타났", "것으로 보인다", "이같은", "에 있어서", "라고 할 수 있", "폭등", "폭락", "충격", "대박")
# 채널별 말투 금지어(청년 채널: 압박·공포 표현, 2026-10-30). 경제는 설정이 없어 그대로.
BANNED_PHRASES += tuple(config.CHANNEL_CONFIG.get("banned_phrases", ()))
TITLE_LINE_MAX = 13  # 템플릿 제목 크기에서 한 줄에 무리 없이 들어가는 글자 수
SENTENCE_MAX = 40
HOOK_MAX = 24  # chat·notify·desktop 표지의 말풍선·알림 한 줄
SENTENCE_FIELDS = ("body",)  # 문장으로 쓰는 필드. label·note·kicker는 명사구 허용
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_POLITE_END_RE = re.compile(r"(요|죠|까|나요)[.!?…]*$")

# --- 2026-10-30 QA 보완 ---
# 기존 숫자 대조는 두 자리 이상만 봐서 "3월 5일", "1억", "5%"처럼 한 자리 숫자가 붙은 날짜·금액이 틀려도 통과했다.
_DATE_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_ARTICLE_DATE_RES = (_DATE_RE, re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)"))  # 원문은 "11.14" 표기도 인정
_UNIT_NUMBER_RE = re.compile(r"(?<![\d.,])(\d(?:\.\d+)?)\s*(억|천만|천|만|%|배|세|살)")
_YMD_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B55\uFE0F]")
KST = timezone(timedelta(hours=9))
NO_EMOJI_ON_CARDS = config.CHANNEL_CONFIG.get("no_emoji_on_cards", False)  # 카드 디자인에 이모지 없는 채널
PLAN_WORDS = tuple(config.CHANNEL_CONFIG.get("plan_notice_words", ()))  # 원문에 있으면 "예정" 표시가 필요한 말
PLAN_MARKERS = ("예정", "정부안", "확정 전", "확정되면", "추진", "검토")
CHECK_DEADLINE = config.CHANNEL_CONFIG.get("check_deadline", False)  # 모집·신청 정보 채널은 마감 지난 기사를 건너뜀
CAPTION_HARD_MAX = 2200  # 인스타그램 캡션 한도(고지·해시태그 포함)


class SkipArticle(Exception):
    """다시 써도 해결되지 않는 문제(이미 마감된 모집 등). 재시도 없이 이 기사를 건너뛴다."""

SYSTEM_PROMPT = config.CHANNEL_CONFIG["writer_intro"] + """
기사 하나를 읽고, 스크롤을 멈추게 하는 캐러셀 슬라이드를 만든다.

[가장 중요한 원칙]
1. 이 기사에서 독자가 꼭 가져가야 할 핵심 메시지 딱 1개를 먼저 정하고, 모든 슬라이드는 그 메시지를 위해서만 존재한다.
2. 핵심과 관계없는 정보(배경 설명, 부수적 인물, 매체 이름, 일반론)는 과감히 버린다. 기사에 있다고 다 쓰지 않는다.
3. 장수는 내용이 정한다. 3~7장. 할 말이 3장이면 3장으로 끝낸다. 같은 말을 나눠서 늘리지 않는다.
4. 숫자는 기사에 나온 숫자만, 기사와 똑같은 값으로 쓴다. 숫자를 만들거나 반올림하거나 계산하지 않는다.
5. "공부해 보세요", "관심을 가져보세요", "투자에 유의하세요" 같은 뻔한 마무리 문장 금지. 투자 권유·종목 추천 금지.
6. 어려운 용어는 핵심을 이해하는 데 꼭 필요할 때만, 한 문장 비유로 짧게 푼다.
7. 한자·일본어 금지. 한글, 숫자, 영어 약어(S&P500, ETF 등)만 쓴다.
8. 말투는 친근한 해요체("~예요", "~했어요"). 한 문장은 짧게. 기사체("~했다", "~로 나타났다") 금지.
9. 같은 숫자는 덱 전체에서 최대 2번만 쓴다. 같은 사실을 장마다 되풀이하지 않는다.
10. 기사에 반전·단서·반론("다만", "하지만", "오히려")이 있으면 반드시 한 장에 담는다. 독자가 오해하지 않게 하는 장이다.
11. 표지 title은 독자가 "왜?" "진짜?"라고 묻게 만드는 한 문장이다. 사실 나열형 제목("○○ 후 ○○") 금지.
12. closing은 표지를 반복하지 않는다. 앞 장들을 다 본 독자에게 남길 한 줄(그래서 무엇을 기억하면 되는지)을 새 표현으로 쓴다.
13. 앞으로 가격이 오르거나 내릴 거라고 암시하지 않는다("오를 가능성", "기대해 볼 만", "염두에 두세요" 금지). 과거 사실과 그 한계만 전한다.
14. 비교할 때는 기사에 나온 가장 의미 있는 기준(평소·다른 해·이전 기간 등)과 비교한다. 서로 다른 자산을 나란히 놓는 것보다 "평소 대비"가 더 설득력 있다.
15. 표본이 적거나 인과가 불확실하다는 내용이 기사에 있으면, 그 한계를 숫자와 함께 분명히 말한다.
{channel_rules}
[슬라이드 종류] 필요한 것만 골라 쓴다. 첫 장은 반드시 cover.
- 모든 title·quote는 의미 단위로 "\\n"을 넣어 줄을 나눈다. 한 줄은 12자 이내(예: "중간선거 끝나면\\n비트코인이 오른다?").
- cover: 표지. title은 궁금증을 만드는 한 문장(최대 24자). kicker는 위에 붙는 짧은 꼬리표(최대 12자). subtitle은 선택(최대 30자).
- stat: 숫자 하나로 말하는 장. value(예: "53.7%"), label(그 숫자가 무엇인지, 최대 30자), note(선택, 최대 50자).
- compare: 두세 값을 나란히 비교. title(최대 22자), items=[{"label": 최대 12자, "value": 최대 10자}], note(선택).
- point: 문장으로 설명하는 장. kicker(선택), title(최대 22자), body(최대 90자, 문장 2개 이내).
- list: 짧은 항목 나열. title(최대 22자), items(2~4개, 각 최대 28자).
- quote: 기사 속 인용·핵심 한 줄을 크게. quote(최대 50자), source(선택).
- closing: 마지막 장. 핵심 메시지를 한 문장으로 다시 꽂는다. title(최대 24자), body(선택, 최대 60자).

[출력] 아래 JSON 객체 하나만 출력한다. 다른 말 금지.
{
  "message": "핵심 메시지 한 문장",
  "slides": [ {"kind": "cover", ...}, ... ],
  "caption": "인스타 캡션 본문(3~5문장, 해시태그 제외)",
  "hashtags": ["{hashtag_example}", "..."],
  "image_prompt": "English. A concrete photographic scene (subject, setting, light, mood) that fits the topic. No text, no logos, no charts."
}
"""
# 채널별 추가 규칙·해시태그 예시. JSON 예시의 중괄호 때문에 format 대신 replace로 채운다.
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("{channel_rules}", "".join(
    f"{i}. {rule}\n" for i, rule in enumerate(config.CHANNEL_CONFIG["writer_rules"], 16)
)).replace("{hashtag_example}", config.CHANNEL_CONFIG["hashtag_example"]).replace(
    "3~7장. 할 말이 3장이면 3장으로", f"{SLIDES_MIN}~{SLIDES_MAX}장. 할 말이 {SLIDES_MIN}장이면 {SLIDES_MIN}장으로"
).replace("인스타 캡션 본문(3~5문장, 해시태그 제외)", CAPTION_SPEC)
# 말투도 채널별. 청년 채널(디징)은 단톡방에서 정보 챙겨주는 친구의 다정한 반말(2026-10-30 운영자 확정). 기본은 해요체.
BANMAL = config.CHANNEL_CONFIG.get("speech") == "banmal"
if BANMAL:
    SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
        '8. 말투는 친근한 해요체("~예요", "~했어요"). 한 문장은 짧게. 기사체("~했다", "~로 나타났다") 금지.',
        f"8. {config.CHANNEL_CONFIG['tone_rule']}")

EDITOR_PROMPT = """너는 인스타그램 카드뉴스 편집장이다. 기자가 쓴 슬라이드 초안을, 휴대폰으로 넘겨 보는 독자가
한 장을 2초 안에 읽고 이해하도록 다듬는다. 사실과 숫자는 원문 그대로 두고, 원문에 없는 정보는 넣지 않는다.

아래 순서로 점검하고 고친다.
1. 구성: 표지(궁금증) → 근거(숫자) → 반전·한계 → 정리로 자연스럽게 이어지는가.
   제목만 이어 읽어도 이야기가 되는가. 힘없는 장, 앞 장과 겹치는 장은 합치거나 뺀다(3~7장 유지).
   한 장에 메시지는 하나만.
2. 문장 구조: 한 문장에 정보 하나. 문장은 35자 이내. 주어와 서술어를 가깝게.
   수식어를 앞에 길게 쌓지 않는다("과거 반감기 이후 12~18개월 안에 큰 폭 상승했으며" 같은 긴 연결문은 둘로 자른다).
   "~며", "~고," 로 문장을 이어 붙이지 않는다.
3. 어감: 모든 문장 끝을 해요체("~예요", "~해요", "~죠", "~어요")로 통일한다.
   합쇼체("~습니다"), 기사체("~했다", "~로 나타났다", "~것으로 보인다"), 번역투("~에 대해", "~에 있어서", "이같은"),
   과장("폭등", "폭락", "충격", "대박")을 없앤다. 친구에게 설명하듯 쉽고 담백하게.
4. 가독성: title·quote는 의미 단위로 "\\n" 줄바꿈해 한 줄 12자 이내. body는 문장 2개 이내.
   kicker는 짧은 명사구. 숫자는 단위와 붙여 쓴다(53.7%, 4년).

출력: 고친 덱 전체를 초안과 같은 JSON 형식으로, 다른 말 없이 출력한다.
"edits" 배열에 무엇을 왜 고쳤는지 짧게 적는다(예: "3장 긴 연결문을 두 문장으로 분리")."""

EDITOR_PROMPT = EDITOR_PROMPT.replace("(3~7장 유지)", f"({SLIDES_MIN}~{SLIDES_MAX}장 유지)")
if BANMAL:
    EDITOR_PROMPT = EDITOR_PROMPT.replace(
        '3. 어감: 모든 문장 끝을 해요체("~예요", "~해요", "~죠", "~어요")로 통일한다.', f"3. 어감: {config.CHANNEL_CONFIG['editor_tone']}"
    ).replace("친구에게 설명하듯 쉽고 담백하게.", "단톡방에서 정보 챙겨주는 친구가 말하듯 짧고 정확하게.")
# 반말 채널은 끝맺음이 다양해 허용 목록 대신 기사체·합쇼체 끝맺음만 막는다.
_ARTICLE_END_RE = re.compile(r"(습니다|습니까|었다|았다|했다|였다|이다|한다|된다|있다|없다|보인다)[.!?…]*$")

LIMITS = {
    "title": 26, "kicker": 14, "subtitle": 34, "label": 32, "value": 12, "note": 56,
    "body": 96, "quote": 54, "source": 24,
}


def normalize_hanja(text: str) -> str:
    for hanja, hangul in HANJA_ABBREVIATIONS.items():
        text = text.replace(hanja, hangul)
    return text


def _texts(slide: dict) -> list[str]:
    texts = [v for k, v in slide.items() if isinstance(v, str) and k != "kind"]
    for item in slide.get("items") or []:
        texts.extend(item.values() if isinstance(item, dict) else [item])
    if isinstance(slide.get("hook"), list):  # 표지 말풍선·알림 문장(테크 채널 템플릿)
        texts.extend(slide["hook"])
    return [t for t in texts if isinstance(t, str)]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text)}


def _article_dates(article: str) -> set[tuple[int, int]]:
    return {(int(m), int(d)) for regex in _ARTICLE_DATE_RES for m, d in regex.findall(article)}


def check_facts(text: str, article: str, where: str) -> None:
    """날짜(월·일)와 한 자리 숫자가 붙은 금액·비율·나이·배수를 원문과 맞춘다(두 자리 이상 숫자는 기존 대조가 본다)."""
    dates = _article_dates(article)
    for m, d in _DATE_RE.findall(text):
        if (int(m), int(d)) not in dates:
            raise ValueError(f"{where} 원문에 없는 날짜 {m}월 {d}일: {text!r}")
    compact = re.sub(r"\s+", "", article)
    for num, unit in _UNIT_NUMBER_RE.findall(text):
        if f"{num}{unit}" not in compact:
            raise ValueError(f"{where} 원문에 없는 수치 {num}{unit}(원문 표기 그대로 쓸 것): {text!r}")


def check_plan_notice(deck: dict, article: str) -> None:
    """원문에 아직 확정 전(정부안·입법예고·시행 예정 등)이라는 말이 있으면 카드나 캡션 어딘가에 그 표시가 있어야 한다."""
    words = [w for w in PLAN_WORDS if w in article]
    if not words:
        return
    texts = " ".join(t for s in deck.get("slides") or [] for t in _texts(s)) + " " + str(deck.get("caption") or "")
    if not any(m in texts for m in PLAN_MARKERS):
        raise ValueError(f"원문에 확정 전 표현({', '.join(words[:3])})이 있는데 카드·캡션에 '예정'·'정부안' 같은 표시가 없음")


def check_deadline(caption: str, today: date | None = None) -> None:
    """캡션의 기간·마감 줄 날짜가 모두 오늘보다 앞이면 이미 끝난 모집이다. 다시 써도 소용없으니 기사를 건너뛴다."""
    today = today or datetime.now(KST).date()
    found = []
    for line in caption.splitlines():
        if not any(k in line for k in ("🗓", "마감", "기간", "접수", "신청")):
            continue
        for y, m, d in _YMD_RE.findall(line):
            try:
                if y:
                    found.append(date(int(y), int(m), int(d)))
                    continue
                guess = date(today.year, int(m), int(d))
                if (today - guess).days > 180:  # 연말에 쓴 "1월 15일"은 내년
                    guess = date(today.year + 1, int(m), int(d))
                elif (guess - today).days > 180:  # 연초에 쓴 "12월 20일"은 작년
                    guess = date(today.year - 1, int(m), int(d))
                found.append(guess)
            except ValueError:
                continue
    if found and max(found) < today:
        raise SkipArticle(f"기간·마감이 이미 지남({max(found).isoformat()})이라 게시하지 않음")


def check_deck(deck: dict, article: str) -> None:
    slides = deck.get("slides") or []
    if not SLIDES_MIN <= len(slides) <= SLIDES_MAX:
        raise ValueError(f"슬라이드 수 {len(slides)}장 ({SLIDES_MIN}~{SLIDES_MAX}장이어야 함)")
    if slides[0].get("kind") != "cover":
        raise ValueError("첫 장이 cover가 아님")
    known = {"cover", "stat", "compare", "point", "list", "quote", "closing"}
    article_numbers = _numbers(article)
    seen: list[str] = []
    number_slides: dict[str, set[int]] = {}
    for i, slide in enumerate(slides, 1):
        if slide.get("kind") not in known:
            raise ValueError(f"{i}장: 알 수 없는 종류 {slide.get('kind')!r}")
        for key in LIMITS:
            if key in slide and slide[key] is not None and not isinstance(slide[key], str):
                raise ValueError(f"{i}장 {key}는 문자열이어야 함(목록·객체 금지): {slide[key]!r}")
        items = slide.get("items")
        if items is not None and not (isinstance(items, list) and all(isinstance(x, (str, dict)) for x in items)):
            raise ValueError(f"{i}장 items 형식 오류: {items!r}")
        hook = slide.get("hook")
        if hook is not None and not (isinstance(hook, list) and len(hook) <= 3
                                     and all(isinstance(h, str) and len(h) <= HOOK_MAX for h in hook)):
            raise ValueError(f"{i}장 hook은 {HOOK_MAX}자 이내 문장 3개 이하의 목록이어야 함: {hook!r}")
        for key, limit in LIMITS.items():
            if isinstance(slide.get(key), str) and len(slide[key]) > limit:
                raise ValueError(f"{i}장 {key} 길이 초과({len(slide[key])}>{limit}): {slide[key]!r}")
        for key in ("title", "quote"):
            for line in (slide.get(key) or "").split("\n"):
                if len(line.strip()) > TITLE_LINE_MAX:
                    raise ValueError(f"{i}장 {key} 한 줄이 {TITLE_LINE_MAX}자 초과(의미 단위로 \\n 줄바꿈 필요): {line!r}")
        for key in SENTENCE_FIELDS:
            for sentence in _SENTENCE_SPLIT_RE.split((slide.get(key) or "").strip()):
                if not sentence:
                    continue
                if len(sentence) > SENTENCE_MAX:
                    raise ValueError(f"{i}장 {key} 문장이 {SENTENCE_MAX}자 초과: {sentence!r}")
                if BANMAL and _ARTICLE_END_RE.search(sentence):
                    raise ValueError(f"{i}장 {key} 기사체·합쇼체 문장(반말로): {sentence!r}")
                if not BANMAL and not _POLITE_END_RE.search(sentence):
                    raise ValueError(f"{i}장 {key} 해요체가 아닌 문장: {sentence!r}")
        for text in _texts(slide):
            banned = next((b for b in BANNED_PHRASES if b in text), None)
            if banned:
                raise ValueError(f"{i}장 금지 표현 {banned!r}: {text!r}")
            if _STRAY_SCRIPT_RE.search(text):
                raise ValueError(f"{i}장 한자/일본어 혼입: {text!r}")
            if NO_EMOJI_ON_CARDS and _EMOJI_RE.search(text):
                raise ValueError(f"{i}장 카드 문구에 이모지(이 채널은 카드에 이모지 금지, 캡션에만): {text!r}")
            check_facts(text, article, f"{i}장")
            # 원문에 없는 숫자 금지(연도·퍼센트·금액 모두). 한 자리 수는 서수·개수라 허용.
            for n in _numbers(text):
                if len(n.replace(".", "")) >= 2 and n not in article_numbers:
                    raise ValueError(f"{i}장 원문에 없는 숫자 {n!r}: {text!r}")
        combined = " ".join(_texts(slide))
        for n in _numbers(combined):
            number_slides.setdefault(n, set()).add(i)
        if any(fuzz.token_sort_ratio(combined, prev) >= 80 for prev in seen):
            raise ValueError(f"{i}장 내용이 앞 장과 거의 같음")
        seen.append(combined)
    overused = [n for n, idx in number_slides.items() if len(n.replace(".", "")) >= 2 and len(idx) > 2]
    if overused:
        raise ValueError(f"같은 숫자를 3장 이상에서 반복: {overused}")
    if slides[-1].get("kind") == "closing" and fuzz.token_sort_ratio(
        slides[-1].get("title", ""), slides[0].get("title", "")
    ) >= 70:
        raise ValueError("마지막 장이 표지 제목을 되풀이함")
    if CHECK_CAPTION:
        check_caption(deck.get("caption"), article_numbers)
        check_facts(deck["caption"], article, "캡션")
    check_plan_notice(deck, article)
    if CHECK_DEADLINE:
        check_deadline(str(deck.get("caption") or ""))


def check_caption(caption, article_numbers: set[str]) -> None:
    """자세한 설명을 캡션에 담는 채널은 대상·금액·기간이 캡션에 들어가므로 슬라이드와 같은 기준으로 본다."""
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError("캡션이 비어 있음")
    if len(caption) > CAPTION_MAX:
        raise ValueError(f"캡션 {len(caption)}자 ({CAPTION_MAX}자 이내여야 함)")
    banned = next((b for b in BANNED_PHRASES if b in caption), None)
    if banned:
        raise ValueError(f"캡션 금지 표현 {banned!r}")
    if _STRAY_SCRIPT_RE.search(caption):
        raise ValueError("캡션에 한자/일본어 혼입")
    made_up = sorted(n for n in _numbers(caption) if len(n.replace(".", "")) >= 2 and n not in article_numbers)
    if made_up:
        raise ValueError(f"캡션에 원문에 없는 숫자 {made_up}")


# 템플릿 장식 문구(창 제목·버튼·필기체 등). 매 게시물이 같은 문구를 되풀이하지 않게 덱마다 모델이 새로 짓는다
# (2026-10-30 운영자: "세부 내용은 컨셉에 맞춰 항상 바뀌어야"). 키는 card_templates/README.md 참고.
PROP_TEXT = {"script": 22, "closing_script": 22, "window_title": 10, "memo_title": 8, "label": 8, "ask": 16}
PROP_PAIRS = {"buttons": 6, "closing_buttons": 6, "folders": 8}
_SCRIPT_RE = re.compile(r"^[a-z][a-z' .,&!?-]*$")  # 영문 필기체 글꼴(Allura)은 한글이 없다


def clean_props(props, article: str) -> dict:
    """틀린 항목은 덱을 다시 쓰게 하지 않고 버린다(템플릿 기본값이 대신 들어감). 장식 문구 하나 때문에
    무료 한도 안에서 덱 전체를 재생성할 가치는 없다. 숫자·금지 표현·한자 기준은 슬라이드와 같다."""
    if not isinstance(props, dict):
        return {}
    article_numbers = _numbers(article)

    def ok(text, limit: int) -> bool:
        return (isinstance(text, str) and 0 < len(text.strip()) <= limit
                and not _STRAY_SCRIPT_RE.search(text) and not any(b in text for b in BANNED_PHRASES)
                and all(len(n.replace(".", "")) < 2 or n in article_numbers for n in _numbers(text)))

    out = {}
    for key, limit in PROP_TEXT.items():
        value = props.get(key)
        if ok(value, limit):
            value = value.strip().lower() if key.endswith("script") else value.strip()
            if not key.endswith("script") or _SCRIPT_RE.match(value):
                out[key] = value
    for key, limit in PROP_PAIRS.items():
        value = props.get(key)
        if isinstance(value, list) and len(value) == 2 and all(ok(v, limit) for v in value):
            out[key] = [v.strip() for v in value]
    return out


def clean_article(text: str) -> str:
    return normalize_hanja(_BOILERPLATE_RE.sub("", text)).strip()


def _parse(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def _raise_if_quota_exhausted(e: Exception) -> None:
    """Cloudflare 무료 한도(하루 1만 뉴런) 소진(429)은 재시도해도 소용없고 다른 카테고리 몫만
    더 막으므로 바로 포기한다(2026-10-29 테스트 중 실제로 소진됨, 09:00 KST에 리셋)."""
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
        raise RuntimeError("Cloudflare Workers AI 일일 무료 한도 소진(429) — 09:00 KST 리셋 후 재시도") from e


def write_deck(title: str, article: str, category: str, hint: str = "") -> dict:
    """초안 작성 → 편집장 검수(구성·문장 구조·어감·가독성) → 코드 검사.
    검사에 걸리면 걸린 이유를 편집 단계에 돌려줘 고치게 하고, 그래도 안 되면 초안부터 다시 쓴다.
    hint: 지난번 렌더에서 걸린 문제(글자 넘침 등). 다시 쓸 때 피하도록 원문 뒤에 붙인다."""
    article = clean_article(article)
    source = normalize_hanja(f"카테고리: {config.CATEGORY_LABELS.get(category, category)}\n제목: {title}\n\n[기사 원문]\n{article}")
    if hint:
        source += f"\n\n[지난번 카드 렌더에서 걸린 문제 — 이번엔 피할 것]\n{hint}"
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            draft = _parse(call_text_model(SYSTEM_PROMPT, source, max_tokens=8000, model=MODEL))
        except Exception as e:  # 파싱 실패, 응답 잘림(incomplete), 일시적 API 오류 모두 다음 시도로
            _raise_if_quota_exhausted(e)
            last_error = e
            print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 초안 파싱 실패: {e}", file=sys.stderr)
            continue
        deck, feedback = draft, ""
        for review in range(1, 4):  # 편집 1회 + 검사 실패 시 수정 최대 2회
            request = f"{source}\n\n[슬라이드 초안]\n{json.dumps(deck, ensure_ascii=False)}"
            if feedback:
                request += f"\n\n[검사에서 걸린 문제 — 반드시 고칠 것]\n{feedback}"
            try:
                deck = _parse(call_text_model(EDITOR_PROMPT, request, max_tokens=8000, model=MODEL))
                check_deck(deck, article + " " + title)
                hashtags = deck.get("hashtags") if isinstance(deck.get("hashtags"), list) else []
                tags = " ".join("#" + str(t).lstrip("#").replace(" ", "") for t in hashtags[:8])
                footer = "\n".join(p for p in (DISCLAIMER, tags) if p)
                final_caption = f"{str(deck.get('caption') or '').strip()}\n\n{footer}".strip()
                if len(final_caption) > CAPTION_HARD_MAX:
                    raise ValueError(f"캡션이 고지·해시태그 포함 {len(final_caption)}자(인스타 한도 {CAPTION_HARD_MAX}자) — 본문을 줄일 것")
            except SkipArticle:
                raise
            except Exception as e:
                _raise_if_quota_exhausted(e)
                last_error, feedback = e, str(e)
                print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 편집 {review}회차 실패: {e}", file=sys.stderr)
                continue
            deck["caption"] = final_caption
            deck.setdefault("image_prompt", draft.get("image_prompt"))
            deck["props"] = clean_props(deck.get("props") or draft.get("props"), article + " " + title)
            return deck
    raise RuntimeError(f"슬라이드 생성 {MAX_ATTEMPTS}회 실패: {last_error}")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    item = json.load(open(src, encoding="utf-8"))
    deck = write_deck(item["title"], item["article"], item["category"])
    deck["source"] = {"title": item["title"], "outlet": item.get("source"), "url": item.get("url")}
    json.dump(deck, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(deck, ensure_ascii=False, indent=1))

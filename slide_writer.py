"""기사 원문 → 카드뉴스 슬라이드 JSON (2026-10-29 내용 가공 재설계, 템플릿 시안용).

기존 generate_cardset.py는 표지·개념 비유·근거·의미·행동 제안·고지를 매번 같은
순서로 채우게 해서, 기사에 없는 내용으로 빈칸을 메우는 일이 잦았다(운영자 피드백:
"불필요한 정보가 질 낮게 들어 있다"). 여기서는:

- 슬라이드 수(3~7)와 종류(cover/stat/compare/point/list/quote/closing)를 기사에
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

from rapidfuzz import fuzz

import config
from llm_client import call_text_model

MAX_ATTEMPTS = 3
# 2026-10-29 비교: llama-3.3-70b는 같은 숫자 반복·기사체·표지 반복이 심해 gpt-oss-120b를 기본으로 둔다.
MODEL = os.environ.get("SLIDE_WRITER_MODEL", "@cf/openai/gpt-oss-120b")
DISCLAIMER = "※ 교육·정보 제공용이며 투자 권유가 아닙니다."

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
                  "공부해 보", "관심을 가져", "투자에 유의", "알아두면 좋")

SYSTEM_PROMPT = """너는 인스타그램 경제 카드뉴스 "묘한 경제"의 에디터다. 독자는 경제 기사를 잘 안 읽는 20~30대다.
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

[슬라이드 종류] 필요한 것만 골라 쓴다. 첫 장은 반드시 cover.
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
  "hashtags": ["경제", "..."],
  "image_prompt": "English. A concrete photographic scene (subject, setting, light, mood) that fits the topic. No text, no logos, no charts."
}
"""

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
    return [t for t in texts if isinstance(t, str)]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text)}


def check_deck(deck: dict, article: str) -> None:
    slides = deck.get("slides") or []
    if not 3 <= len(slides) <= 7:
        raise ValueError(f"슬라이드 수 {len(slides)}장 (3~7장이어야 함)")
    if slides[0].get("kind") != "cover":
        raise ValueError("첫 장이 cover가 아님")
    known = {"cover", "stat", "compare", "point", "list", "quote", "closing"}
    article_numbers = _numbers(article)
    seen: list[str] = []
    number_slides: dict[str, set[int]] = {}
    for i, slide in enumerate(slides, 1):
        if slide.get("kind") not in known:
            raise ValueError(f"{i}장: 알 수 없는 종류 {slide.get('kind')!r}")
        for key, limit in LIMITS.items():
            if isinstance(slide.get(key), str) and len(slide[key]) > limit:
                raise ValueError(f"{i}장 {key} 길이 초과({len(slide[key])}>{limit}): {slide[key]!r}")
        for text in _texts(slide):
            banned = next((b for b in BANNED_PHRASES if b in text), None)
            if banned:
                raise ValueError(f"{i}장 금지 표현 {banned!r}: {text!r}")
            if _STRAY_SCRIPT_RE.search(text):
                raise ValueError(f"{i}장 한자/일본어 혼입: {text!r}")
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


def clean_article(text: str) -> str:
    return normalize_hanja(_BOILERPLATE_RE.sub("", text)).strip()


def write_deck(title: str, article: str, category: str) -> dict:
    article = clean_article(article)
    user = normalize_hanja(f"카테고리: {config.CATEGORY_LABELS.get(category, category)}\n제목: {title}\n\n[기사 원문]\n{article}")
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = call_text_model(SYSTEM_PROMPT, user, max_tokens=4000, model=MODEL).strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
        try:
            deck = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            check_deck(deck, article + " " + title)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 실패: {e}", file=sys.stderr)
            continue
        tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in deck.get("hashtags", [])[:8])
        deck["caption"] = f"{deck.get('caption', '').strip()}\n\n{DISCLAIMER}\n{tags}".strip()
        return deck
    raise RuntimeError(f"슬라이드 생성 {MAX_ATTEMPTS}회 실패: {last_error}")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    item = json.load(open(src, encoding="utf-8"))
    deck = write_deck(item["title"], item["article"], item["category"])
    deck["source"] = {"title": item["title"], "outlet": item.get("source"), "url": item.get("url")}
    json.dump(deck, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(deck, ensure_ascii=False, indent=1))

"""승인된 뉴스 원문을 읽고 "묘한 경제" 브랜드의 3~5장 카드뉴스 content.json을 만든다.

기존 카드 1장(FLUX 배경+제목)짜리 파이프라인을 대체한다. 기사 원문을
스크래핑해 실제 사실을 근거로, 표지+핵심 1~3장+고지 구조의 카드 세트를
LLM으로 생성하고, assets/templates/validate.js로 검증한다.

실행:
    python generate_cardset.py
"""

import json
import logging
import subprocess
from datetime import date
from pathlib import Path

import config
from db import get_client
from fetch_article import fetch_article_text
from llm_client import call_text_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate_cardset")

CHARACTER_DIR = Path("assets/character")
EXISTING_POSES = sorted(p.stem for p in CHARACTER_DIR.glob("*.png"))
ALLOWED_VISUALS = {"question", "hub", "bars", "timeline", "doc-up", "piggy-up", "pair", "flow", "alert"}
VISUAL_REQUIRED_FIELDS = {
    "hub": ["main", "a", "b"],
    "bars": ["items"],
    "timeline": ["points"],
    "flow": ["a", "b"],
}

SEASON_BY_MONTH = {
    3: "spring", 4: "spring", 5: "spring",
    6: "jangma", 7: "jangma",
    8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter", 1: "winter", 2: "winter",
}

MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """너는 인스타그램 경제 카드뉴스 계정 "묘한 경제"(@myohan.economy)의 콘텐츠 기획자다.
어려운 경제 뉴스를 20대 사회초년생이 이해하기 쉽게 3~5장짜리 카드 세트로 만든다.

## 절대 규칙
1. 카드 안의 모든 숫자·사실은 아래 [기사 원문]에 실제로 등장하는 내용만 사용한다. 원문에 없는 숫자를 지어내지 마라.
2. 어려운 용어는 한 줄로 풀어 설명한다. 클릭베이트 금지. 친근한 해요체.
3. 개인 맞춤 투자·재무 조언을 하지 않는다.
4. 카드에는 출처 URL을 넣지 않는다.

## 출력 형식 (JSON만 출력, 설명 텍스트 붙이지 마라)
{
  "cards": [
    {"type":"cover","badge":"짧고 후킹되는 배지 문구(이모지 1개 포함, 20자 이내)","hook":"핵심 한 줄(20자 이내)","stat":"핵심 숫자(있으면, 예: 2.75%)","statArrow":"↑ 또는 ↓(stat 있을 때만)","sub":"부제(선택, 20자 이내)","char":"포즈이름"},
    {"type":"why 또는 concept 또는 meaning","num":"①②③ 중 하나(concept일 때만, 선택)","eyebrow":"소제목(선택)","title":"카드 제목(36자 이내)","body":"본문(90자 이내, 문장 사이 <br><br> 가능)","char":"포즈이름","visual":{...}},
    ... (이런 카드 1~3장) ...
    {"type":"action","eyebrow":"오늘 해볼 것 소개 문구","items":["실천 항목1","실천 항목2"],"char":"포즈이름"},
    {"type":"disclaimer","brand":"출처 & 안내","text":"이 콘텐츠는 교육·정보 제공용이며, 특정 투자 권유나 개인 맞춤 재무 조언이 아닙니다.","note":"자세한 출처는 캡션에서 확인하세요","handle":"@myohan.economy"}
  ],
  "caption":"인스타그램 캡션(후킹 한 줄 + 본문 사실 요약 + 핵심 takeaway, 해시태그 최대 5개 포함)"
}

카드는 반드시 첫 번째가 cover, 마지막이 disclaimer여야 하고, 전체 3~5장이어야 한다.
action 카드는 선택사항(있으면 disclaimer 바로 앞에 배치).

## 사용 가능한 고양이 포즈(char 필드에 이 중 하나만 사용, 아래 목록에 없는 이름 금지)
__POSES__

포즈 선택 가이드: 부정적/리스크 내용→cat-worried, 판단이 필요한 내용→cat-thinking,
좋은 소식→cat-cheer, 저축·투자·소비→cat-money, 설명하는 카드→cat-explain,
놀람·반전→cat-surprised, 마무리·실천→cat-happy, 기본→cat-default.

## 사용 가능한 시각화 타입(visual 필드, 선택사항)
- {"type":"question"} : 물음표 강조
- {"type":"hub","main":"핵심어","a":"연결1","b":"연결2"}
- {"type":"bars","items":[{"h":30,"label":"라벨1"},{"h":32,"label":"라벨2","hi":true}]} (h는 상대적 크기 숫자, 최대 1장에만 사용)
- {"type":"timeline","points":[{"label":"값1"},{"label":"값2","now":true}]}
- {"type":"doc-up"} : 서류+상승 화살표
- {"type":"piggy-up"} : 저금통+상승 화살표
- {"type":"pair"} : 서류&저금통 나란히
- {"type":"flow","a":"항목1","b":"항목2"} : A→B 흐름
- {"type":"alert","label":"주의"} : 경고 아이콘

카테고리: __CATEGORY__
"""


def current_season() -> str:
    return SEASON_BY_MONTH[date.today().month]


def fetch_approved_without_cardset() -> list[dict]:
    client = get_client()
    approved = (
        client.table("news_items")
        .select("id,title,url,category")
        .eq("status", "approved")
        .execute()
        .data
        or []
    )
    if not approved:
        return []
    existing = (
        client.table("cards")
        .select("news_item_id")
        .in_("news_item_id", [it["id"] for it in approved])
        .execute()
        .data
        or []
    )
    done_ids = {row["news_item_id"] for row in existing}
    return [it for it in approved if it["id"] not in done_ids]


def _truncate(text: str, max_chars: int) -> str:
    """단어 경계에서 자르고 말줄임표를 붙이되, 최종 길이가 반드시 max_chars 이내가 되도록 한다."""
    if text is None:
        return text
    plain = text.replace("<br><br>", " ").replace("<br>", " ").strip()
    if len(plain) <= max_chars:
        return plain
    budget = max_chars - 1
    cut = plain[:budget]
    last_space = cut.rfind(" ")
    if last_space > budget * 0.5:
        cut = cut[:last_space]
    return cut.rstrip(" ,.-·") + "…"


def sanitize_cards(data: dict) -> dict:
    """소형 LLM이 글자수 제한을 못 지키는 경우가 많아, 검증 전에 코드로 강제
    절삭하는 안전망(카드 제목 요약과 동일한 역할 분리 원칙: LLM=품질, 코드=제약)."""
    concept_num = 0
    for card in data.get("cards", []):
        if card.get("type") == "cover" and not card.get("badge"):
            card["badge"] = "오늘의 경제 🐾"
        if card.get("hook"):
            card["hook"] = _truncate(card["hook"], 20)
        if card.get("sub"):
            card["sub"] = _truncate(card["sub"], 20)
        if card.get("title"):
            card["title"] = _truncate(card["title"], 36)
        if card.get("body"):
            card["body"] = _truncate(card["body"], 90)
        if card.get("stat"):
            # stat-num은 128px 폰트로 표지에 크게 박히는 요소라 아주 짧아야
            # 한다(예: "2.75%"). 한글 단어가 섞이면 글자 수가 적어도 실제
            # 렌더 폭이 넓어 넘치는(overflow) 경우가 실측으로 반복 확인돼,
            # 한글 2자 이상이 섞이거나 6자를 넘으면 아예 통째로 제거한다.
            hangul_count = sum("가" <= ch <= "힣" for ch in card["stat"])
            if hangul_count >= 2 or len(card["stat"]) > 6:
                card.pop("stat", None)
                card.pop("statArrow", None)
            else:
                card["stat"] = card["stat"][:6]
        if card.get("char") not in EXISTING_POSES:
            card["char"] = "cat-default"

        if card.get("type") == "disclaimer":
            # 고지문은 매번 LLM에게 그대로 재현해달라고 맡기지 않고 고정값으로 강제한다
            # (문구가 조금만 달라져도 validate.js가 정확 일치를 요구해 실패했었음).
            card["brand"] = "출처 & 안내"
            card["text"] = "이 콘텐츠는 교육·정보 제공용이며, 특정 투자 권유나 개인 맞춤 재무 조언이 아닙니다."
            card["note"] = "자세한 출처는 캡션에서 확인하세요"
            card["handle"] = "@myohan.economy"

        visual = card.get("visual")
        if visual is not None:
            valid = (
                isinstance(visual, dict)
                and visual.get("type") in ALLOWED_VISUALS
                and all(visual.get(f) for f in VISUAL_REQUIRED_FIELDS.get(visual.get("type"), []))
            )
            if not valid:
                card.pop("visual", None)  # 형식이 안 맞으면 없느니만 못하니 제거(선택 필드)

        if card.get("type") in ("why", "concept", "meaning") and card.get("num"):
            concept_num += 1
            card["num"] = "①②③④⑤"[min(concept_num, 5) - 1]
    return data


def build_content_json(title: str, article_text: str, category: str) -> dict:
    label = config.CATEGORY_LABELS.get(category, category)
    system_prompt = SYSTEM_PROMPT.replace(
        "__POSES__", "\n".join(f"- {p}" for p in EXISTING_POSES)
    ).replace("__CATEGORY__", label)
    article_block = article_text if article_text else "(원문을 가져오지 못함, 제목 정보만으로 신중하게 작성)"
    user_content = f"제목: {title}\n\n[기사 원문]\n{article_block}"

    raw = call_text_model(system_prompt, user_content, max_tokens=1200)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    data["season"] = current_season()
    data["handle"] = "@myohan.economy"
    return sanitize_cards(data)


def validate_content(content_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["node", "assets/templates/validate.js", str(content_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return result.returncode == 0, (result.stdout + result.stderr)


def render_cardset(content_path: Path, out_dir: Path) -> bool:
    result = subprocess.run(
        ["node", "assets/templates/render.js", str(content_path), str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        log.error("렌더 실패: %s", (result.stdout or "") + (result.stderr or ""))
        return False
    log.info((result.stdout or "").strip())
    return True


def process_item(item: dict) -> dict | None:
    log.info("[%s] 기사 원문 수집 중: %s", item["category"], item["url"])
    article_text = fetch_article_text(item["url"])
    log.info("[%s] 원문 %d자 확보", item["category"], len(article_text))

    caption = None
    content = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = build_content_json(item["title"], article_text, item["category"])
            caption = data.pop("caption", "")
            content = data
        except Exception as e:
            log.warning("[%s] LLM 응답 파싱 실패(%d/%d): %s", item["category"], attempt, MAX_ATTEMPTS, e)
            continue

        out_dir = Path("output") / date.today().isoformat() / item["category"]
        out_dir.mkdir(parents=True, exist_ok=True)
        content_path = out_dir / "content.json"
        content_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

        ok, message = validate_content(content_path)
        if not ok:
            log.warning("[%s] 검증 실패(%d/%d): %s", item["category"], attempt, MAX_ATTEMPTS, message)
            continue

        log.info("[%s] 검증 통과 (%d/%d)", item["category"], attempt, MAX_ATTEMPTS)
        if not render_cardset(content_path, out_dir):
            log.warning("[%s] 렌더 실패(오버플로 등), 콘텐츠 재생성으로 재시도(%d/%d)", item["category"], attempt, MAX_ATTEMPTS)
            continue
        (out_dir / "caption.txt").write_text(caption or "", encoding="utf-8")
        return {"content_path": content_path, "out_dir": out_dir, "caption": caption}

    log.warning(
        "[%s] %d회 시도 후에도 LLM 카드세트를 만들지 못해 최소 폴백으로 대체",
        item["category"], MAX_ATTEMPTS,
    )
    return build_and_render_fallback(item, article_text)


def build_fallback_content(item: dict, article_text: str) -> dict:
    """LLM이 계속 실패할 때도 카테고리가 그날 통째로 빠지지 않도록, 제목과
    원문 첫 문장만으로 구조 규칙을 100% 만족하는 최소 카드 3장을 코드로 만든다."""
    label = config.CATEGORY_LABELS.get(item["category"], item["category"])
    first_sentence = (article_text.split(".")[0].strip() + "." if article_text else item["title"])[:90]
    return {
        "season": current_season(),
        "handle": "@myohan.economy",
        "cards": [
            {
                "type": "cover",
                "badge": "오늘의 경제 🐾",
                "hook": _truncate(item["title"], 20),
                "char": "cat-default",
            },
            {
                "type": "why",
                "eyebrow": label,
                "title": _truncate(item["title"], 36),
                "body": _truncate(first_sentence, 90),
                "char": "cat-explain",
            },
            {
                "type": "disclaimer",
                "brand": "출처 & 안내",
                "text": "이 콘텐츠는 교육·정보 제공용이며, 특정 투자 권유나 개인 맞춤 재무 조언이 아닙니다.",
                "note": "자세한 출처는 캡션에서 확인하세요",
                "handle": "@myohan.economy",
            },
        ],
    }


def build_and_render_fallback(item: dict, article_text: str) -> dict | None:
    content = build_fallback_content(item, article_text)
    out_dir = Path("output") / date.today().isoformat() / item["category"]
    out_dir.mkdir(parents=True, exist_ok=True)
    content_path = out_dir / "content.json"
    content_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, message = validate_content(content_path)
    if not ok:
        log.error("[%s] 폴백 카드세트도 검증 실패(코드 버그 가능성): %s", item["category"], message)
        return None
    if not render_cardset(content_path, out_dir):
        log.error("[%s] 폴백 카드세트 렌더도 실패, 완전히 건너뜀", item["category"])
        return None

    caption = f"{item['title']}\n\n#경제 #카드뉴스 #묘한경제"
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    return {"content_path": content_path, "out_dir": out_dir, "caption": caption}


def main():
    client = get_client()
    items = fetch_approved_without_cardset()
    log.info("카드세트 생성 대상: %d건", len(items))

    publish_order = (client.table("cards").select("id", count="exact").execute().count or 0) + 1

    for item in items:
        result = process_item(item)
        if not result:
            continue

        png_count = len(list(result["out_dir"].glob("[0-9][0-9].png")))
        client.table("cards").insert(
            {
                "news_item_id": item["id"],
                "category": item["category"],
                "final_title": item["title"][: config.TITLE_MAX_CHARS],
                "image_path": str(result["out_dir"]),
                "publish_order": publish_order,
                "published": False,
            }
        ).execute()
        publish_order += 1
        log.info("[%s] 카드세트 생성 완료: %s (%d장)", item["category"], result["out_dir"], png_count)


if __name__ == "__main__":
    main()

"""검증 통과한 배경 이미지 위에 제목을 합성해 카테고리당 1장, 총 3장의 카드를 만든다.

카테고리별로 vision_check_passed=true인 이미지 중 승인된 뉴스에 해당하는 것을 골라
output/YYYY-MM-DD/card_{category}.png로 저장하고 cards 테이블에 기록한다.

실행:
    python compose_card.py
"""

import logging
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("compose_card")

CARD_SIZE = (1080, 1350)
OVERLAY_HEIGHT_RATIO = 0.38
TITLE_FONT_SIZE = 64
CATEGORY_FONT_SIZE = 34


def truncate_title(title: str, max_chars: int = config.TITLE_MAX_CHARS) -> str:
    """원문 의미를 최대한 보존하며 max_chars 이내로 축약한다.

    단순 하드 절삭이 아니라 단어(공백) 경계에서 자르고 말줄임표를 붙이되,
    최종 결과가 반드시 max_chars 이내가 되도록 검증한다.
    """
    title = title.strip()
    if len(title) <= max_chars:
        return title

    ellipsis = "…"
    budget = max_chars - len(ellipsis)
    if budget <= 0:
        return title[:max_chars]

    truncated = title[:budget]
    last_space = truncated.rfind(" ")
    if last_space > budget * 0.5:
        truncated = truncated[:last_space]

    result = truncated.rstrip(" ,.-·") + ellipsis
    if len(result) > max_chars:
        result = title[:budget].rstrip() + ellipsis
    assert len(result) <= max_chars, "제목 축약 결과가 글자수 제한을 초과함"
    return result


def fetch_card_source(category: str) -> dict | None:
    client = get_client()
    news_items = (
        client.table("news_items")
        .select("id,title")
        .eq("category", category)
        .eq("status", "approved")
        .execute()
        .data
        or []
    )
    if not news_items:
        return None

    news_ids = [n["id"] for n in news_items]
    images = (
        client.table("generated_images")
        .select("id,news_item_id,image_path,vision_check_passed")
        .in_("news_item_id", news_ids)
        .eq("vision_check_passed", True)
        .order("id", desc=True)
        .execute()
        .data
        or []
    )
    if not images:
        return None

    image = images[0]
    news = next(n for n in news_items if n["id"] == image["news_item_id"])
    return {"news_item_id": news["id"], "title": news["title"], "image_path": image["image_path"]}


def wrap_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """실제 픽셀 폭을 측정해가며 글자 단위로 그리디하게 줄바꿈한다."""
    lines: list[str] = []
    current = ""
    for ch in title:
        candidate = current + ch
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def compose(background_path: str, title: str, category: str, output_path: Path):
    bg = Image.open(background_path).convert("RGB")
    bg = bg.resize(CARD_SIZE, Image.LANCZOS)

    overlay_height = int(CARD_SIZE[1] * OVERLAY_HEIGHT_RATIO)
    overlay = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.rectangle(
        [(0, CARD_SIZE[1] - overlay_height), CARD_SIZE], fill=(0, 0, 0, 170)
    )
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(bg)
    title_font = ImageFont.truetype(config.FONT_BOLD_PATH, TITLE_FONT_SIZE)
    category_font = ImageFont.truetype(config.FONT_PATH, CATEGORY_FONT_SIZE)

    padding = 64
    max_text_width = CARD_SIZE[0] - padding * 2

    label = config.CATEGORY_LABELS.get(category, category)
    label_y = CARD_SIZE[1] - overlay_height + 40
    draw.text((padding, label_y), label, font=category_font, fill=(255, 200, 80))

    lines = wrap_title(draw, title, title_font, max_text_width)
    line_height = TITLE_FONT_SIZE + 14
    total_text_height = line_height * len(lines)
    text_y = CARD_SIZE[1] - padding - total_text_height

    for i, line in enumerate(lines):
        draw.text((padding, text_y + i * line_height), line, font=title_font, fill=(255, 255, 255))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(output_path, "PNG")


def main():
    out_dir = Path("output") / date.today().isoformat()
    client = get_client()

    for order, category in enumerate(config.CATEGORIES, start=1):
        source = fetch_card_source(category)
        if not source:
            log.warning("[%s] 합성할 검증 통과 이미지 없음, 건너뜀", category)
            continue

        final_title = truncate_title(source["title"])
        output_path = out_dir / f"card_{category}.png"

        try:
            compose(source["image_path"], final_title, category, output_path)
        except Exception as e:
            log.error("[%s] 카드 합성 실패: %s", category, e)
            continue

        client.table("cards").insert(
            {
                "news_item_id": source["news_item_id"],
                "category": category,
                "final_title": final_title,
                "image_path": str(output_path),
                "publish_order": order,
                "published": False,
            }
        ).execute()
        log.info("[%s] 카드 생성 완료: %s", category, output_path)


if __name__ == "__main__":
    main()

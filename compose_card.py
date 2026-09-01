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
from generate_prompt import summarize_title

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("compose_card")

CARD_SIZE = (1080, 1350)
GRADIENT_START_RATIO = 0.42
GRADIENT_MAX_ALPHA = 235
GRADIENT_COLOR = (8, 10, 20)
TITLE_FONT_SIZE = 96
CATEGORY_FONT_SIZE = 32
ACCENT_COLOR = (255, 196, 64)
CHIP_TEXT_COLOR = (26, 22, 10)
PADDING = 60
BOX_COLOR = (6, 8, 14)
BOX_ALPHA = 170
BOX_TOP_PAD = 36


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


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    return draw.textbbox((0, 0), text, font=font)[2]


def _greedy_char_wrap(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in title:
        candidate = current + ch
        if _text_width(draw, candidate, font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def wrap_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """가급적 짧은 한 글자짜리 잔여 줄이 생기지 않도록, 단어 경계에서 두 줄로
    균형 있게 나눈다(폭 차이가 가장 작은 분할점 선택). 적당한 분할점이 없으면
    글자 단위 그리디 줄바꿈으로 대체한다."""
    if _text_width(draw, title, font) <= max_width:
        return [title]

    words = title.split(" ")
    best_split, best_diff = None, None
    for i in range(1, len(words)):
        line1, line2 = " ".join(words[:i]), " ".join(words[i:])
        w1, w2 = _text_width(draw, line1, font), _text_width(draw, line2, font)
        if w1 <= max_width and w2 <= max_width:
            diff = abs(w1 - w2)
            if best_diff is None or diff < best_diff:
                best_diff, best_split = diff, [line1, line2]
    if best_split:
        return best_split

    return _greedy_char_wrap(draw, title, font, max_width)


def make_gradient_overlay(size: tuple[int, int]) -> Image.Image:
    """카드 하단이 어두워지는 부드러운 그라데이션 오버레이(하드 엣지 박스 대신)."""
    width, height = size
    grad_start = int(height * GRADIENT_START_RATIO)
    grad_height = height - grad_start

    gradient = Image.new("L", (1, grad_height), 0)
    for y in range(grad_height):
        t = y / max(grad_height - 1, 1)
        gradient.putpixel((0, y), int(GRADIENT_MAX_ALPHA * (t**1.6)))
    gradient = gradient.resize((width, grad_height))

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    color_layer = Image.new("RGBA", (width, grad_height), GRADIENT_COLOR + (255,))
    color_layer.putalpha(gradient)
    overlay.paste(color_layer, (0, grad_start), color_layer)
    return overlay


def draw_centered_text_with_shadow(
    draw: ImageDraw.ImageDraw, center_x: int, y: int, text: str, font: ImageFont.FreeTypeFont, fill
):
    bbox = draw.textbbox((0, 0), text, font=font)
    x = center_x - (bbox[2] - bbox[0]) // 2
    shadow_offset = max(2, font.size // 30)
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def compose(background_path: str, title: str, category: str, output_path: Path):
    bg = Image.open(background_path).convert("RGB")
    bg = bg.resize(CARD_SIZE, Image.LANCZOS)
    bg = Image.alpha_composite(bg.convert("RGBA"), make_gradient_overlay(CARD_SIZE)).convert("RGB")

    draw = ImageDraw.Draw(bg)
    title_font = ImageFont.truetype(config.FONT_BOLD_PATH, TITLE_FONT_SIZE)
    category_font = ImageFont.truetype(config.FONT_BOLD_PATH, CATEGORY_FONT_SIZE)

    max_text_width = CARD_SIZE[0] - PADDING * 2
    center_x = CARD_SIZE[0] // 2

    lines = wrap_title(draw, title, title_font, max_text_width)
    line_height = int(TITLE_FONT_SIZE * 1.22)
    total_title_height = line_height * len(lines)

    label = config.CATEGORY_LABELS.get(category, category)
    label_bbox = draw.textbbox((0, 0), label, font=category_font)
    label_w, label_h = label_bbox[2] - label_bbox[0], label_bbox[3] - label_bbox[1]
    chip_pad_x, chip_pad_y = 22, 12
    chip_w, chip_h = label_w + chip_pad_x * 2, label_h + chip_pad_y * 2
    chip_gap = 28

    title_top = CARD_SIZE[1] - PADDING - total_title_height
    chip_y0 = title_top - chip_gap - chip_h
    chip_x0 = center_x - chip_w // 2

    # 배경이 밝거나 복잡해도 글씨가 또렷이 보이도록, 카테고리 라벨~제목 구간 전체에
    # 반투명 검정 박스를 깔아 그라데이션만으로는 부족한 대비를 확실히 확보한다.
    box_top = max(0, chip_y0 - BOX_TOP_PAD)
    box_layer = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(box_layer).rectangle(
        [(0, box_top), CARD_SIZE], fill=BOX_COLOR + (BOX_ALPHA,)
    )
    bg = Image.alpha_composite(bg.convert("RGBA"), box_layer).convert("RGB")
    draw = ImageDraw.Draw(bg)

    draw.rounded_rectangle(
        [chip_x0, chip_y0, chip_x0 + chip_w, chip_y0 + chip_h], radius=chip_h // 2, fill=ACCENT_COLOR
    )
    draw.text(
        (chip_x0 + chip_pad_x, chip_y0 + chip_pad_y - label_bbox[1]),
        label,
        font=category_font,
        fill=CHIP_TEXT_COLOR,
    )

    for i, line in enumerate(lines):
        draw_centered_text_with_shadow(
            draw, center_x, title_top + i * line_height, line, title_font, (255, 255, 255)
        )

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

        try:
            summarized = summarize_title(source["title"], category, config.TITLE_MAX_CHARS)
        except Exception as e:
            log.warning("[%s] 제목 요약 실패(%s), 원문을 잘라서 사용", category, e)
            summarized = source["title"]
        final_title = truncate_title(summarized)  # 글자수 제한을 반드시 지키도록 하는 안전망
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

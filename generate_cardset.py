"""선정된 뉴스 원문으로 카드뉴스 캐러셀을 만든다.

2026-10-29부터 내용과 디자인을 분리했다:
- 내용: slide_writer.py가 기사 원문에서 채널 설정 장수(경제 3~7장, 청년 1~4장)의 슬라이드를 만든다(고정 페이지 없음,
  원문에 없는 숫자·투자 암시·한자 등은 코드로 차단).
- 디자인: card_templates/의 템플릿 6종을 날짜·카테고리 순서로 돌려 쓴다(pick_template).
  배경은 템플릿 톤에 맞춰 flux로 한 장 만들고, 마스코트는 채널 브랜드(경제는 brands/myohan.json의 고양이 포즈).

이전 방식(표지·개념 비유·근거·의미·행동·고지를 매번 같은 순서로 채우는 card-template-v2)은
운영자 피드백("불필요한 정보가 질 낮게 들어 있다")으로 대체했다. git 기록에 남아 있다.

실행:
    python generate_cardset.py
"""

import json
import logging
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageStat

import config
import events
import reel
import slide_writer
from card_templates import images as template_images
from card_templates.make_job import build_job, split_variant
from db import get_client
from fetch_article import fetch_article_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate_cardset")

KST = timezone(timedelta(hours=9))
TEMPLATES_DIR = Path("card_templates")
MAX_RENDER_ATTEMPTS = 2  # 글자 넘침이면 슬라이드를 다시 써서 한 번 더 렌더


def pick_template(category: str, day: date | None = None, slot: int = 0) -> str:
    """하루 3개 게시물이 서로 다른 템플릿이 되고, 같은 카테고리도 날마다 바뀌도록
    (날짜 순번 + 카테고리 순번)으로 config.CARD_TEMPLATES를 돈다.
    하루 게시 수를 정한 채널(daily_posts)은 카테고리와 상관없이 그날 몇 번째 게시물인지(slot)로 돈다."""
    day = day or datetime.now(KST).date()
    templates = config.CARD_TEMPLATES
    daily_posts = config.CHANNEL_CONFIG.get("daily_posts")
    if daily_posts:
        return templates[(day.toordinal() * daily_posts + slot) % len(templates)]
    return templates[(day.toordinal() + config.CATEGORIES.index(category)) % len(templates)]


def fetch_approved_without_cardset() -> list[dict]:
    client = get_client()
    approved_since = (datetime.now(timezone.utc) - timedelta(hours=config.MAX_AGE_HOURS)).isoformat()
    approved = (
        client.table("news_items")
        .select("id,title,url,category,published_at")
        .eq("status", "approved")
        .in_("category", list(config.CATEGORIES))
        .gte("collected_at", approved_since)
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


def check_rendered_images(out_dir: Path, expected_count: int) -> None:
    pngs = sorted(out_dir.glob("[0-9][0-9].png"))
    if len(pngs) != expected_count:
        raise ValueError(f"QA: 렌더 이미지 수 불일치({len(pngs)}/{expected_count})")
    for image_path in pngs:
        with Image.open(image_path) as image:
            if image.size != (1080, 1350):
                raise ValueError(f"QA: 잘못된 이미지 크기 {image_path.name}: {image.size}")
            if max(ImageStat.Stat(image.convert("RGB")).var) < 2:
                raise ValueError(f"QA: 빈 이미지 의심: {image_path.name}")


def make_background(subject: str, template: str, out_dir: Path) -> Path:
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    prompt = f"{subject}, {template_images.STYLE_SUFFIX[split_variant(template)[0]]}"
    last_error: Exception | None = None
    with httpx.Client(headers=headers, transport=httpx.HTTPTransport(local_address="0.0.0.0")) as client:
        for _ in range(2):
            try:
                return template_images.generate(prompt, out_dir / "background.jpg", client)
            except Exception as e:
                last_error = e
    raise RuntimeError(f"배경 이미지 생성 실패: {last_error}")


def render(template: str, deck: dict, background: Path, out_dir: Path) -> tuple[bool, str]:
    for old in out_dir.glob("[0-9][0-9].png"):  # 이전 시도의 장수가 더 많았을 때 남는 파일 제거
        old.unlink()
    (out_dir / "deck.json").write_text(json.dumps(deck, ensure_ascii=False, indent=1), encoding="utf-8")
    job = build_job(template, out_dir / "deck.json", config.BRAND_PATH, background)
    # 렌더 QA 강도(render.js). strict면 장식 속 글자 넘침·잘림도 실패 처리(청년 채널), 아니면 경고만.
    job["qa"] = {"strict": bool(config.CHANNEL_CONFIG.get("render_strict", False))}
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    result = subprocess.run(
        ["node", str(TEMPLATES_DIR / "render.js"), str(job_path), str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    output = (result.stdout + result.stderr).strip()
    for line in output.splitlines():
        if line.startswith("warning: "):
            log.warning("[렌더 QA 경고] %s", line[len("warning: "):])
    return result.returncode == 0, output


def _record_retry(item: dict, attempt: int, reason: str) -> None:
    events.record(
        "cardset", "retry", category=item["category"], news_item_id=item["id"],
        message=f"({attempt}/{MAX_RENDER_ATTEMPTS}) {reason}",
    )


def process_item(item: dict, slot: int = 0) -> dict | None:
    category = item["category"]
    template = pick_template(category, slot=slot)
    log.info("[%s] 기사 원문 수집 중 (템플릿 %s): %s", category, template, item["url"])
    events.record("cardset", "started", category=category, news_item_id=item["id"], message=item["title"],
                  detail={"template": template})
    article_text = fetch_article_text(item["url"])
    if len(article_text.strip()) < config.QA_MIN_ARTICLE_CHARS:
        # Google News 리다이렉트가 가끔 SSL 오류로 끊긴다(2026-10-29 실측) → 한 번 더 시도
        article_text = fetch_article_text(item["url"]) or article_text
    log.info("[%s] 원문 %d자 확보", category, len(article_text))

    def fail(message: str) -> None:
        log.error("[%s] %s", category, message)
        events.record("cardset", "failed", category=category, news_item_id=item["id"], message=message)

    if len(article_text.strip()) < config.QA_MIN_ARTICLE_CHARS:
        fail(f"원문이 QA 최소 분량 미달로 게시하지 않음 ({len(article_text.strip())}자)")
        return None

    # 하루 게시 수를 정한 채널은 같은 카테고리가 하루 2건일 수 있어 기사 ID로 폴더를 나눈다.
    folder = f"{category}-{item['id']}" if config.CHANNEL_CONFIG.get("daily_posts") else category
    out_dir = Path("output") / date.today().isoformat() / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    background = None
    hint = ""  # 렌더에서 걸린 이유를 다음 작성에 알려줘 같은 실수를 반복하지 않게 한다
    # "29일"처럼 일만 쓴 날짜를 푸는 기준(기사 발행일, KST). 없으면 slide_writer가 오늘로 본다.
    published = (datetime.fromisoformat(item["published_at"]).astimezone(KST).date()
                 if item.get("published_at") else None)
    for attempt in range(1, MAX_RENDER_ATTEMPTS + 1):
        try:
            deck = slide_writer.write_deck(item["title"], article_text, category, hint=hint, published=published)
        except slide_writer.SkipArticle as e:
            fail(f"QA: {e}")
            return None
        except Exception as e:
            fail(f"슬라이드 내용 생성 실패로 게시하지 않음: {e}")
            return None
        try:
            background = background or make_background(deck.get("image_prompt") or item["title"], template, out_dir)
        except Exception as e:
            fail(str(e))
            return None

        ok, output = render(template, deck, background, out_dir)
        if not ok:
            log.warning("[%s] 렌더 실패(%d/%d): %s", category, attempt, MAX_RENDER_ATTEMPTS, output[-300:])
            _record_retry(item, attempt, f"렌더 실패: {output[-200:]}")
            hint = f"카드에 글자가 칸을 넘치거나 잘렸다. 아래 장의 문구를 더 짧게 쓸 것.\n{output[-400:]}"
            continue
        try:
            check_rendered_images(out_dir, len(deck["slides"]))
        except ValueError as e:
            _record_retry(item, attempt, f"렌더 QA 실패: {e}")
            hint = str(e)
            continue
        (out_dir / "caption.txt").write_text(deck["caption"], encoding="utf-8")
        if config.CHANNEL_CONFIG.get("reels"):
            try:
                reel_path = reel.make_reel(out_dir, config.CHANNEL_DIR / "music")
                if not reel_path.exists() or reel_path.stat().st_size < 10_000:
                    raise RuntimeError(f"릴스 파일이 비었거나 너무 작음: {reel_path}")
            except Exception as e:  # 릴스가 안 돼도 캐러셀은 올린다
                (out_dir / "reel.mp4").unlink(missing_ok=True)  # 깨진 영상이 게시 단계에서 올라가지 않게
                log.warning("[%s] 릴스 영상 만들기 실패(캐러셀만 게시): %s", category, e)
                events.record("reel", "failed", category=category, news_item_id=item["id"], message=f"영상 만들기 실패: {e}")
        log.info("[%s] 렌더 통과 (%d장, 템플릿 %s)", category, len(deck["slides"]), template)
        return {"out_dir": out_dir, "caption": deck["caption"], "template": template}

    fail(f"렌더 {MAX_RENDER_ATTEMPTS}회 실패로 게시하지 않음")
    return None


def main():
    client = get_client()
    items = fetch_approved_without_cardset()
    log.info("카드세트 생성 대상: %d건", len(items))

    publish_order = (client.table("cards").select("id", count="exact").execute().count or 0) + 1

    for slot, item in enumerate(items):
        result = process_item(item, slot)
        if not result:
            continue

        pngs = sorted(result["out_dir"].glob("[0-9][0-9].png"))
        card = client.table("cards").insert(
            {
                "news_item_id": item["id"],
                "category": item["category"],
                "final_title": item["title"][: config.TITLE_MAX_CHARS],
                "image_path": str(result["out_dir"]),
                "publish_order": publish_order,
                "published": False,
            }
        ).execute().data[0]
        publish_order += 1
        log.info("[%s] 카드세트 생성 완료: %s (%d장)", item["category"], result["out_dir"], len(pngs))

        # 대시보드 [카드] 미리보기용. 게시 단계도 같은 경로에 upsert하므로 중복 업로드는 무해하다.
        try:
            from instagram_client import upload_to_storage
            for png in pngs:
                upload_to_storage(str(png))
        except Exception as e:
            log.warning("[%s] 미리보기 이미지 업로드 실패(게시에는 영향 없음): %s", item["category"], e)
        events.record(
            "cardset", "success", category=item["category"], count=len(pngs), news_item_id=item["id"],
            card_id=card["id"], message=f"카드 {len(pngs)}장 렌더링 완료 · {result['template']}",
            detail={"template": result["template"]},
        )


if __name__ == "__main__":
    main()

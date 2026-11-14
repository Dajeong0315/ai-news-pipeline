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
from PIL import Image, ImageFilter, ImageStat

import auto_select
import card_preview
import config
import events
import llm_client
import reel
import schedule_plan
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
# 배경 사진 속 글자(깨진 한글 간판 등)를 비전 모델로 검사해 다시 뽑는다. 채널 설정 "check_background_text"(디징 켬)
BG_TEXT_CHECK = bool(config.CHANNEL_CONFIG.get("check_background_text"))
BG_TEXT_ATTEMPTS = 3  # 그래도 글자가 나오면 흐림 처리
BG_BLUR_RADIUS = 24
# 순위 대체(2026-11-02, 경제): 선정 기사가 카드 작성 단계에서 떨어지면 오늘 순위의 다음 기사로 다시 만든다.
# 하루 시도는 max_daily_attempts(경제 3)번까지. 텍스트 모델을 부른 시도만 센다(오늘 usage 기록, 수동 재실행 포함).
# 원문을 못 받아 모델 호출 없이 끝난 시도는 뉴런을 안 써서 세지 않는다.
FALLBACK = auto_select.RANK_SIZE > 1 and not auto_select.LISTINGS
MAX_DAILY_ATTEMPTS = int(config.CHANNEL_CONFIG.get("max_daily_attempts") or 0)
QUOTA_EXHAUSTED = False  # Cloudflare 일일 무료 한도 소진이면 다음 순위도 소용없으니 멈춘다(일시 제한 429는 아님)
# 디자이너 검수 비용 안전장치(2026-11-02): 그날 계정 사용량이 이 값을 넘으면 검수를 건너뛴다(무료 한도 1만, 09:00 KST 초기화).
DESIGNER_NEURON_CAP = int(config.CHANNEL_CONFIG.get("designer_neuron_cap") or 7000)
RUN_RECORDED = 0.0  # 이번 실행에서 usage 이벤트로 이미 남긴 뉴런(남기지 않은 사용량 = llm_client.NEURONS_USED - 이것)


def pick_template(category: str, day: date | None = None, slot: int = 0) -> str:
    """같은 날 게시물끼리 다른 템플릿이 되고 날마다 바뀌도록 config.CARD_TEMPLATES를 돈다.
    하루 게시 수를 정한 채널(daily_posts, 경제 1·디징 2)은 그날 몇 번째 카드인지(slot)로,
    정하지 않은 채널(옛 방식)은 (날짜 순번 + 카테고리 순번)으로 돈다."""
    day = day or datetime.now(KST).date()
    templates = config.CARD_TEMPLATES
    daily_posts = config.CHANNEL_CONFIG.get("daily_posts")
    if daily_posts:
        return templates[(day.toordinal() * daily_posts + slot) % len(templates)]
    return templates[(day.toordinal() + config.CATEGORIES.index(category)) % len(templates)]


def fetch_approved_without_cardset() -> list[dict]:
    if auto_select.LISTINGS:  # 공고는 며칠 전에 수집된 행도 선정되므로 수집 시각 대신 마감일로 거른다
        return auto_select.approved_listings_without_card()
    client = get_client()
    approved_since = (datetime.now(timezone.utc) - timedelta(hours=config.MAX_AGE_HOURS)).isoformat()
    approved = (
        client.table("news_items")
        .select("id,title,url,source,category,published_at")
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
    # 채널별 배경 보정(디징: 2026-11-01 "street photograph"에 일본어 간판 거리가 나옴). 경제는 설정 없음 → 그대로
    if suffix := config.CHANNEL_CONFIG.get("image_prompt_suffix"):
        prompt += f", {suffix}"
    last_error: Exception | None = None
    path = None
    with httpx.Client(headers=headers, transport=httpx.HTTPTransport(local_address="0.0.0.0")) as client:
        for _ in range(BG_TEXT_ATTEMPTS if BG_TEXT_CHECK else 2):
            try:
                path = template_images.generate(prompt, out_dir / "background.jpg", client)
            except Exception as e:
                last_error = e
                continue
            if not BG_TEXT_CHECK:
                return path
            try:
                has_text, where = llm_client.image_has_text(path)
            except Exception as e:  # 검사 못 한 사진은 믿지 않는다 → 아래 흐림 처리
                log.warning("배경 글자 검사 실패: %s", e)
                break
            if not has_text:
                return path
            log.warning("배경 사진에 글자가 보여 다시 만듦(%s)", where)
    if path is None:
        raise RuntimeError(f"배경 이미지 생성 실패: {last_error}")
    # 몇 번 뽑아도 글자가 나오면 크게 흐려 글자로 안 읽히게 한다(색·분위기는 남는다)
    log.warning("배경 글자를 못 피해서 흐림 처리: %s", path)
    events.record("cardset", "retry", message=f"배경 사진 글자를 {BG_TEXT_ATTEMPTS}번 안에 못 피해서 흐림 처리함")
    Image.open(path).convert("RGB").filter(ImageFilter.GaussianBlur(BG_BLUR_RADIUS)).save(path, quality=92)
    return path


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


LISTING_OFFICIAL_MAX = 2500  # 공식 페이지 본문은 공고 원문 보충용이라 짧게
_NO_FETCH_HOSTS = ("forms.gle", "docs.google.com", "vo.la", "bit.ly")  # 신청서·단축링크는 본문이 없다


def _korean_date(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{d.year}년 {d.month}월 {d.day}일"


def listing_article(item: dict, days_left: int) -> str:
    """공고 카드의 원문 = 정규화한 공고 정보 + raw_text(+ 공식 페이지를 읽을 수 있으면 그 본문). 기사는 읽지 않는다.
    날짜는 "2026년 11월 12일" 표기로 넣어야 카드 날짜 대조(slide_writer.check_facts)가 원문 날짜로 인정한다."""
    info = item.get("listing") or {}
    period = f"{_korean_date(item['apply_start'])} ~ " if item.get("apply_start") else ""
    header = "\n".join(line for line in (
        f"공고 제목: {item['title']}",
        f"주최·기관: {item.get('source') or info.get('host') or ''}",
        f"대상: {info.get('target')}" if info.get("target") else "",
        f"혜택: {info.get('benefit')}" if info.get("benefit") else "",
        f"신청 기간: {period}{_korean_date(item['apply_end'])}까지 (오늘 기준 D-{days_left})",
        f"지역: {info.get('region')}" if info.get("region") else "",
        f"공식 페이지: {item['official_url']}" if item.get("official_url") else "",
    ) if line)
    text = f"{header}\n\n{info.get('raw_text') or ''}".strip()
    url = item.get("official_url") or ""
    if url.startswith("http") and not any(h in url for h in _NO_FETCH_HOSTS):
        official = fetch_article_text(url)[:LISTING_OFFICIAL_MAX]
        if len(official) >= 200:
            text += f"\n\n[공식 페이지 본문]\n{official}"
    return text


def process_item(item: dict, slot: int = 0, history: list[dict] | None = None, folder_suffix: str = "", revision_request: str = "") -> dict | None:
    category = item["category"]
    template = pick_template(category, slot=slot)
    is_listing = item.get("source_type") == "listing"
    log.info("[%s] %s 원문 수집 중 (템플릿 %s): %s", category, "공고" if is_listing else "기사", template, item["url"])
    events.record("cardset", "started", category=category, news_item_id=item["id"], message=item["title"],
                  detail={"template": template})

    def fail(message: str, status: str | None = None) -> None:
        global QUOTA_EXHAUSTED
        QUOTA_EXHAUSTED = QUOTA_EXHAUSTED or "일일 무료 한도" in message
        log.error("[%s] %s", category, message)
        events.record("cardset", "failed", category=category, news_item_id=item["id"], message=message)
        if status:  # 공고는 다음 날에도 후보로 남으므로, 다시 해도 안 될 실패는 상태를 바꿔 반복 선정·뉴런 소모를 막는다
            get_client().table("news_items").update({"status": status}).eq("id", item["id"]).execute()

    # 중복 게시 차단(2026-10-31): 최근 7일 카드 + 이번 실행에서 이미 만든 카드와 같은 기사면 원문을 읽기 전에 건너뛴다
    if not is_listing and history is not None and (reason := auto_select.duplicate_reason(item, history)):
        fail(f"QA: 중복 차단 — {reason}")
        return None
    # 주제 필터 이전에 선정돼 카드 없이 남은 기사(10/31 첫 실행: 전날 선정된 코스피 시황·KB금융 실적 3건)도 모델 호출 전에 거른다
    if not is_listing and (pattern := auto_select.excluded_topic(item["title"])):
        fail(f"QA: 주제 필터 — 제목 패턴 {pattern!r}")
        return None

    listing = None
    if is_listing:
        # 마감은 카드 문구가 아니라 apply_end 값으로 결정한다: 지났거나 3일 미만이면 만들지 않는다
        days_left = (date.fromisoformat(item["apply_end"]) - datetime.now(KST).date()).days if item.get("apply_end") else None
        if days_left is None or days_left < auto_select.LISTING_MIN_DAYS_LEFT:
            fail(f"QA: 마감까지 {days_left}일이라 게시하지 않음(3일 이상 남아야 함)", status="expired")
            return None
        listing = {"host": item.get("source") or (item.get("listing") or {}).get("host"), "days_left": days_left}
        article_text = listing_article(item, days_left)
    else:
        article_text = fetch_article_text(item["url"])
        if len(article_text.strip()) < config.QA_MIN_ARTICLE_CHARS:
            # Google News 리다이렉트가 가끔 SSL 오류로 끊긴다(2026-10-29 실측) → 한 번 더 시도
            article_text = fetch_article_text(item["url"]) or article_text
    log.info("[%s] 원문 %d자 확보", category, len(article_text))

    # 공고는 다음 날에도 후보로 남아서, 순위 대체 채널은 떨어진 기사를 같은 날 다시 고르지 않게 상태를 바꾼다.
    # 그 밖의 기사는 24시간 지나면 저절로 후보에서 빠진다.
    give_up = "rejected" if is_listing or FALLBACK else None
    if len(article_text.strip()) < config.QA_MIN_ARTICLE_CHARS:
        fail(f"원문이 QA 최소 분량 미달로 게시하지 않음 ({len(article_text.strip())}자)", status=give_up)
        return None

    # 하루 게시 수를 정한 채널은 같은 카테고리가 하루 2건일 수 있어 기사 ID로 폴더를 나눈다.
    folder = f"{category}-{item['id']}" if config.CHANNEL_CONFIG.get("daily_posts") else category
    folder += folder_suffix  # 다시 만든 카드는 -r1·-r2 폴더(옛 카드 파일·Storage 미리보기를 덮어쓰지 않게)
    out_dir = Path("output") / datetime.now(KST).date().isoformat() / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    background = None
    hint = revision_request.strip()  # 텔레그램 수정 요청을 다음 카드 작성에 그대로 전달한다
    # "29일"처럼 일만 쓴 날짜를 푸는 기준(기사 발행일, KST). 없으면 slide_writer가 오늘로 본다.
    published = (datetime.fromisoformat(item["published_at"]).astimezone(KST).date()
                 if item.get("published_at") else None)
    for attempt in range(1, MAX_RENDER_ATTEMPTS + 1):
        try:
            deck = slide_writer.write_deck(item["title"], article_text, category, hint=hint, published=published,
                                           listing=listing, outlet=None if is_listing else item.get("source"))
        except slide_writer.SkipArticle as e:
            fail(f"QA: {e}", status=give_up)
            return None
        except Exception as e:
            # Cloudflare 429(일일 한도·일시 제한)는 공고 탓이 아니니 선정 상태로 두고 다음 실행에서 다시 만든다
            fail(f"슬라이드 내용 생성 실패로 게시하지 않음: {e}", status=None if "429" in str(e) else give_up)
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
        if slide_writer.WRITER_V2 and deck.get("fact_table"):
            deck = designer_pass(item, deck, output, article_text, published, listing, template, background, out_dir)
        (out_dir / "caption.txt").write_text(deck["caption"], encoding="utf-8")
        if config.CHANNEL_CONFIG.get("reels"):
            try:
                reel_path = reel.make_reel(out_dir, config.CHANNEL_DIR / "music", deck)
                if not reel_path.exists() or reel_path.stat().st_size < 10_000:
                    raise RuntimeError(f"릴스 파일이 비었거나 너무 작음: {reel_path}")
                seconds = reel.duration(reel_path)
                if not reel.MIN_SEC <= seconds <= reel.MAX_SEC:
                    raise RuntimeError(f"릴스 길이 {seconds:.1f}초({reel.MIN_SEC}~{reel.MAX_SEC}초여야 함)")
                log.info("[%s] 릴스 %.1f초 · %.1fMB", category, seconds, reel_path.stat().st_size / 1e6)
            except Exception as e:  # 릴스가 안 돼도 캐러셀은 올린다
                (out_dir / "reel.mp4").unlink(missing_ok=True)  # 깨진 영상이 게시 단계에서 올라가지 않게
                log.warning("[%s] 릴스 영상 만들기 실패(캐러셀만 게시): %s", category, e)
                events.record("reel", "failed", category=category, news_item_id=item["id"], message=f"영상 만들기 실패: {e}")
        log.info("[%s] 렌더 통과 (%d장, 템플릿 %s)", category, len(deck["slides"]), template)
        return {"out_dir": out_dir, "caption": deck["caption"], "template": template}

    fail(f"렌더 {MAX_RENDER_ATTEMPTS}회 실패로 게시하지 않음", status=give_up)
    return None


def account_neurons_today() -> float:
    """이 채널 계정의 오늘 텍스트 모델 사용량 추정: 오늘(UTC 0시 = 09:00 KST 초기화 기준) usage 이벤트 합 + 이번 실행에서
    아직 기록하지 않은 사용량(선정 등). Cloudflare 사용량 API는 토큰 권한이 없어(11/2 확인) 우리 기록으로 센다. 배경 이미지 뉴런은 빠진다."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (get_client().table("pipeline_events").select("detail").eq("channel", config.CHANNEL).eq("stage", "usage")
            .gte("created_at", start.isoformat()).execute().data or [])
    recorded = sum(float((r.get("detail") or {}).get("neurons") or 0) for r in rows)
    return recorded + max(0.0, llm_client.NEURONS_USED - RUN_RECORDED)


def record_usage(item: dict, neurons: float, message: str, **detail) -> None:
    global RUN_RECORDED
    RUN_RECORDED += neurons
    events.record("usage", "success", category=item["category"], news_item_id=item["id"], message=message,
                  detail={"neurons": round(neurons, 1), "run_total": round(llm_client.NEURONS_USED, 1), **detail})


def designer_pass(item: dict, deck: dict, render_output: str, article_text: str, published, listing, template: str,
                  background: Path, out_dir: Path) -> dict:
    """렌더 뒤 10년차 디자이너 검수 1회(v2, 2026-11-02). 검사를 통과한 고친 문장만 반영해 다시 렌더하고,
    다시 렌더가 실패하면 원래 덱으로 되돌린다. 검수 3개와 뉴런은 deck.json(designer_review·designer_neurons)에 남는다."""
    try:
        used = account_neurons_today()
    except Exception as e:
        used, note = None, f"사용량 조회 실패({e})"
    if used is not None and used > DESIGNER_NEURON_CAP:
        note = f"오늘 사용량 약 {used:.0f}뉴런 > {DESIGNER_NEURON_CAP}이라 디자이너 검수 건너뜀"
    if used is None or used > DESIGNER_NEURON_CAP:  # 조회가 안 되면 안전한 쪽(건너뜀)
        log.warning("[%s] %s", item["category"], note)
        events.record("cardset", "skipped", category=item["category"], news_item_id=item["id"], message=note)
        deck["designer_review"] = [{"error": note}]
        (out_dir / "deck.json").write_text(json.dumps(deck, ensure_ascii=False, indent=1), encoding="utf-8")
        return deck
    qa = "\n".join(line for line in render_output.splitlines() if line.startswith(("warning:", "overflow")))
    outlet = None if listing else item.get("source")
    try:
        reviewed, review, neurons = slide_writer.designer_review(deck, qa, article_text, item["title"], published, listing, outlet)
    except Exception as e:  # 검수가 안 돼도 카드는 그대로 쓴다(일일 한도 소진은 다음 단계에서 다시 걸린다)
        log.warning("[%s] 디자이너 검수 실패(검수 없이 진행): %s", item["category"], e)
        deck["designer_review"] = [{"error": str(e)[:200]}]
        (out_dir / "deck.json").write_text(json.dumps(deck, ensure_ascii=False, indent=1), encoding="utf-8")
        return deck
    log.info("[%s] 디자이너 검수 %.0f 뉴런(오늘 계정 약 %.0f), 반영 %d/%d", item["category"], neurons, used + neurons,
             sum(1 for r in review if r.get("applied")), len(review))
    record_usage(item, neurons, f"디자이너 검수 {neurons:.0f} 뉴런", stage="designer_review")
    final = deck
    if reviewed is not deck:
        reviewed.update(designer_review=review, designer_neurons=round(neurons, 1))
        ok, output = render(template, reviewed, background, out_dir)
        try:
            if not ok:
                raise ValueError(output[-200:])
            check_rendered_images(out_dir, len(reviewed["slides"]))
            final = reviewed
        except ValueError as e:
            log.warning("[%s] 검수 반영 뒤 렌더 실패로 원래 덱으로 되돌림: %s", item["category"], e)
            for row in review:
                if row.get("applied"):
                    row.update(applied=False, rejected="반영 뒤 렌더 실패로 되돌림")
            render(template, deck, background, out_dir)
    final["designer_review"], final["designer_neurons"] = review, round(neurons, 1)
    (out_dir / "deck.json").write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    return final


def save_card(item: dict, result: dict, publish_order: int, regenerated_from: int | None = None, note: str = "",
              schedule: dict | None = None) -> dict:
    """cards 행을 넣고 미리보기 이미지를 Storage에 올린다. 승인이 필요한 채널(디징)은 pending으로 넣고 텔레그램 승인 요청을 보낸다.
    schedule(전날 제작): {"scheduled_for": 게시 예정 시각, "reserve": 예비분 여부} → 행에 저장하고 미리보기에 표시."""
    client = get_client()
    pngs = sorted(result["out_dir"].glob("[0-9][0-9].png"))
    row = {
        "news_item_id": item["id"],
        "category": item["category"],
        "final_title": item["title"][: config.TITLE_MAX_CHARS],
        "image_path": str(result["out_dir"]),
        "publish_order": publish_order,
        "published": False,
    }
    if card_preview.required():
        row["approval"] = "pending"
    if regenerated_from:
        row["regenerated_from"] = regenerated_from
    if schedule:
        row["scheduled_for"] = schedule.get("scheduled_for")
        row["reserve"] = bool(schedule.get("reserve"))
        note = " · ".join(x for x in (schedule_plan.label(schedule), note) if x)
    card = client.table("cards").insert(row).execute().data[0]
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
        detail={"template": result["template"], "regenerated_from": regenerated_from},
    )
    if card_preview.required():
        card_preview.send_preview(card["id"], result["out_dir"], item["id"], note=note)
    return card


def made_today() -> int:
    """오늘 카드로 만든 공고 수(다시 만들기는 같은 공고라 1건). 하루 두 번 실행하는 채널의 템플릿 순번용."""
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = (get_client().table("cards").select("news_item_id").in_("category", list(config.CATEGORIES))
            .gte("created_at", start.isoformat()).execute().data or [])
    return len({r["news_item_id"] for r in rows if r.get("news_item_id")})


def attempts_today() -> int:
    """오늘 이 채널에서 텍스트 모델을 부른 카드 시도 수(pipeline_events usage, 뉴런 > 0)."""
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = (get_client().table("pipeline_events").select("detail")
            .eq("channel", config.CHANNEL).eq("stage", "usage").gte("created_at", start.isoformat()).execute().data or [])
    return sum(1 for r in rows if ((r.get("detail") or {}).get("neurons") or 0) > 0
               and (r.get("detail") or {}).get("stage") not in ("designer_review", "regenerate"))  # 검수·다시 만들기는 순위 시도가 아니다


def main(plan: list[dict] | None = None):
    """plan(전날 제작, run_daily nextday): schedule_plan.plan_for가 준 빈 자리 목록. 만든 카드에 순서대로 붙이고 다 채우면 멈춘다."""
    client = get_client()
    items = fetch_approved_without_cardset()
    log.info("카드세트 생성 대상: %d건%s", len(items), f" (채울 자리 {len(plan)}개)" if plan is not None else "")
    if plan is not None and not plan:
        return

    publish_order = (client.table("cards").select("id", count="exact").execute().count or 0) + 1
    # 12:00·19:00에 하나씩 만드는 채널(cards_per_run)은 두 번째 카드가 다른 템플릿이 되게 오늘 만든 수부터 센다
    slot = made_today() if config.CHANNEL_CONFIG.get("cards_per_run") and plan is None else 0
    plan = list(plan) if plan is not None else None

    # 같은 실행 안에서 만든 카드끼리도 중복 검사(기사는 최근 7일 카드 + 이번 실행분, 공고는 선정 단계에서 이미 거름)
    history = auto_select.recent_posted_items() if not auto_select.LISTINGS else None
    queue, tried = list(items), set()
    while queue:
        item = queue.pop(0)
        if FALLBACK and MAX_DAILY_ATTEMPTS and (done := attempts_today()) >= MAX_DAILY_ATTEMPTS:
            log.warning("오늘 카드 만들기 %d번 시도로 하루 한도(%d번)를 다 써서 멈춤", done, MAX_DAILY_ATTEMPTS)
            events.record("cardset", "skipped", message=f"하루 시도 {MAX_DAILY_ATTEMPTS}번을 다 씀", detail={"attempts": done})
            break
        tried.add(item["id"])
        neurons_before = llm_client.NEURONS_USED
        result = process_item(item, slot, history)
        neurons = llm_client.NEURONS_USED - neurons_before
        log.info("[%s] 이번 시도 텍스트 모델 %.0f 뉴런(실행 누적 %.0f)", item["category"], neurons, llm_client.NEURONS_USED)
        designer = float((json.loads((Path(result["out_dir"]) / "deck.json").read_text(encoding="utf-8")).get("designer_neurons")
                          if result and (Path(result["out_dir"]) / "deck.json").exists() else 0) or 0)
        record_usage(item, neurons - designer, f"카드 시도 텍스트 모델 {neurons - designer:.0f} 뉴런")  # 검수분은 따로 기록됨
        slot += 1
        if not result:
            if FALLBACK and not QUOTA_EXHAUSTED and (nxt := auto_select.next_ranked(tried)):
                queue.append(nxt)
            continue
        if history is not None:
            history.append(item)
        save_card(item, result, publish_order, schedule=plan.pop(0) if plan else None)
        publish_order += 1
        if plan is not None and not plan:
            break


def regenerate(card: dict) -> dict | None:
    """[다시 만들기] 요청(cards.approval='regenerate') 카드를 같은 공고로 새로 만들고 다시 승인 요청한다.
    같은 공고 최대 max_regenerations(2)회. 옛 카드는 replaced. 실패하면 옛 카드를 pending으로 되돌려 다시 고를 수 있게 한다."""
    client = get_client()
    limit = int(config.CHANNEL_CONFIG.get("max_regenerations") or 2)
    done = (client.table("cards").select("id").eq("news_item_id", card["news_item_id"])
            .not_.is_("regenerated_from", "null").execute().data or [])
    if len(done) >= limit:
        client.table("cards").update({"approval": "pending"}).eq("id", card["id"]).execute()
        events.record("approval", "skipped", card_id=card["id"], message=f"다시 만들기 {limit}회를 다 써서 기존 카드로 되돌림")
        _notify(f"다시 만들기 {limit}회를 다 써서 card {card['id']}를 그대로 둠([게시] 또는 [건너뛰기]로 골라줘)", card["id"])
        return None
    rows = client.table("news_items").select("*").eq("id", card["news_item_id"]).execute().data or []
    if not rows:
        client.table("cards").update({"approval": "rejected"}).eq("id", card["id"]).execute()
        return None
    item = rows[0]
    n = len(done) + 1
    log.info("[%s] card %s 다시 만들기 %d/%d: %s", item["category"], card["id"], n, limit, item["title"])
    neurons_before = llm_client.NEURONS_USED
    result = process_item(item, slot=made_today() + n, folder_suffix=f"-r{n}", revision_request=card.get("revision_request") or "")
    neurons = llm_client.NEURONS_USED - neurons_before  # 다시 만들기도 그날 사용량에 넣는다(검수분은 따로 기록됨)
    designer = float((json.loads((Path(result["out_dir"]) / "deck.json").read_text(encoding="utf-8")).get("designer_neurons")
                      if result and (Path(result["out_dir"]) / "deck.json").exists() else 0) or 0)
    record_usage(item, neurons - designer, f"다시 만들기 텍스트 모델 {neurons - designer:.0f} 뉴런", stage="regenerate")
    if not result:
        client.table("cards").update({"approval": "pending"}).eq("id", card["id"]).execute()
        _notify(f"card {card['id']} 다시 만들기 실패, 기존 카드로 되돌림(이유는 대시보드 오늘 탭 기록)", card["id"])
        return None
    publish_order = (client.table("cards").select("id", count="exact").execute().count or 0) + 1
    # 전날 제작 카드는 게시 예정 시각·예비분 표시를 그대로 이어받는다
    schedule = ({"scheduled_for": card.get("scheduled_for"), "reserve": card.get("reserve")}
                if card.get("scheduled_for") or card.get("reserve") else None)
    new = save_card(item, result, publish_order, regenerated_from=card["id"], note=f"다시 만든 카드 {n}/{limit}",
                    schedule=schedule)
    client.table("cards").update({"approval": "replaced"}).eq("id", card["id"]).execute()
    return new


def _notify(text: str, card_id: int) -> None:
    try:
        import telegram_client
        telegram_client.send_message(text, reply_markup=telegram_client.card_keyboard(card_id))
    except Exception as e:
        log.warning("텔레그램 알림 실패: %s", e)


if __name__ == "__main__":
    main()

"""오늘 만든 카드세트(cards.published=false)를 캐러셀로 인스타그램에 업로드한다.

오늘(KST) 만든 카드만 올린다. 날짜 제한이 없으면 테스트나 실패 후 남은 지난 카드가
다음 정기 실행 때 한꺼번에 게시된다(2026-09-03 미발행 9장 동시 게시 사고와 같은 경로).

승인이 필요한 채널(디징 require_approval, 2026-11-02)은 cards.approval='approved'인 카드만, 만든 지 3일 안의 것만 올린다.
10분 작업(--approved-only)은 [다시 만들기] 요청도 처리한다. 게시 직전에 캡션 D-n을 게시일 기준으로 다시 계산하고
바뀌면 릴스도 다시 만든다. 마감이 3일 안으로 들어온 카드는 올리지 않고 건너뜀 처리한다.
deck.json(또는 channel.json)의 collaborators가 있으면 공동 작업자로 넣고, 우리 다른 채널이면 그 채널 토큰으로 초대를 수락한다.

cards.image_path는 generate_cardset.py가 만든 폴더 경로(01.png~NN.png +
caption.txt가 들어있는)를 가리킨다. 폴더 하나 = 인스타그램 캐러셀 게시물 하나.

실행:
    python publish_instagram.py
    python publish_instagram.py --card-id 62 --dry-run   # 카드 하나만, 게시 대상만 출력
    python publish_instagram.py --card-id 62             # 카드 하나만 게시
    CHANNEL=youth python publish_instagram.py --approved-only [--dry-run]   # 디징 10분 작업
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import comment_dm
import config
import events
import instagram_client
import schedule_plan
import share_to_story
import story_queue
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_instagram")


KST = timezone(timedelta(hours=9))


APPROVED_MAX_DAYS = 3  # 승인 카드는 만든 지 3일 안의 것만(오래 묵은 승인분이 한꺼번에 올라가지 않게)
LISTING_MIN_DAYS_LEFT = 3  # auto_select와 같은 기준: 마감까지 3일 이상 남아야 게시
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
ACCOUNT_KEYS = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID")


def requires_approval() -> bool:
    return bool(config.CHANNEL_CONFIG.get("require_approval"))


def fetch_unpublished(card_id: int | None = None) -> list[dict]:
    client = get_client()
    today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start - timedelta(days=APPROVED_MAX_DAYS - 1) if requires_approval() else today_start
    cols = "id,news_item_id,category,final_title,image_path,publish_order"
    if schedule_plan.enabled():  # 전날 제작 채널만 새 칸을 읽는다(SQL 2026-11-14-nextday-schedule 필요)
        cols += ",scheduled_for,reserve,created_at"
    query = (
        client.table("cards")
        .select(cols)
        .eq("published", False)
        .in_("category", list(config.CATEGORIES))
        .gte("created_at", since.astimezone(timezone.utc).isoformat())
    )
    query = query.or_("publish_status.is.null,publish_status.eq.ready")
    if requires_approval():  # 승인 안 된 카드는 어떤 경로로도 올라가지 않게
        query = query.eq("approval", "approved")
    if card_id is not None:  # 운영자가 검토한 카드 하나만(2026-11-01 디징 첫 게시: 같은 날 만든 다른 카드가 올라가지 않게)
        query = query.eq("id", card_id)
    rows = query.order("publish_order").execute().data or []
    if schedule_plan.enabled() and card_id is None:  # 예정 시각 전 카드는 어떤 경로로도 미리 올라가지 않게
        rows = [r for r in rows if not schedule_plan.not_due(r)]
    return rows


def fetch_regenerate_requests() -> list[dict]:
    cols = "id,news_item_id,category,final_title,image_path,revision_request"
    if schedule_plan.enabled():  # 다시 만든 카드가 게시 예정 시각·예비분 표시를 이어받게
        cols += ",scheduled_for,reserve"
    return (get_client().table("cards").select(cols)
            .eq("published", False).eq("approval", "regenerate").in_("category", list(config.CATEGORIES))
            .order("id").execute().data or [])


def parse_args(argv: list[str]) -> tuple[int | None, bool, bool]:
    """--card-id N: 그 카드만 올린다. --dry-run: 게시 대상만 출력하고 끝낸다. --approved-only: 승인 흐름 10분 작업."""
    card_id = int(argv[argv.index("--card-id") + 1]) if "--card-id" in argv else None
    return card_id, "--dry-run" in argv, "--approved-only" in argv


def posting_slots() -> tuple[int, str]:
    """이번 실행에서 올릴 수 있는 게시물 수와 이유(2026-10-31 몰아 게시 방지).
    9~10월 경제 채널이 하루 3~7개를 3분 간격으로 몰아 올리고 1~2주 비는 일이 반복됐다. 스케줄러가 몇 번 돌든
    - 하루 게시 수 = 채널 daily_posts(경제 1, 디징 2)를 넘지 않고
    - 게시 간격이 채널 min_post_gap_hours(디징 6)보다 짧으면 올리지 않으며
    - 한 번 실행에 1개만 올린다(연속 게시 금지). 디징 두 번째 게시물은 다음 예약 시각(publish_times) 실행이 올린다.
    오늘 게시 수는 pipeline_events의 게시 성공 기록으로 센다. 조회에 실패하면 올리지 않는다(안전한 쪽)."""
    daily = int(config.CHANNEL_CONFIG.get("daily_posts") or 0)
    gap = float(config.CHANNEL_CONFIG.get("min_post_gap_hours") or 0)
    if not daily:  # 하루 게시 수를 정하지 않은 채널(옛 방식)은 제한 없음
        return 99, ""
    now = datetime.now(KST)
    since = min(now.replace(hour=0, minute=0, second=0, microsecond=0), now - timedelta(hours=gap))
    rows = (
        get_client().table("pipeline_events").select("created_at")
        .eq("channel", config.CHANNEL).eq("stage", "publish").eq("status", "success")
        .gte("created_at", since.astimezone(timezone.utc).isoformat()).order("created_at", desc=True)
        .execute().data or []
    )
    times = [datetime.fromisoformat(r["created_at"]).astimezone(KST) for r in rows]
    today = sum(1 for t in times if t.date() == now.date())
    if today >= daily:
        return 0, f"오늘 이미 {today}건 게시(하루 {daily}건)"
    if gap and times and now - times[0] < timedelta(hours=gap):
        return 0, f"마지막 게시 {times[0]:%H:%M}에서 {gap:g}시간이 안 지남"
    return 1, ""


def collaborators_for(out_dir: Path) -> list[str]:
    """deck.json의 collaborators(Aside가 넘긴 콜라보 카드), 없으면 channel.json collaborators. 사용자명만, 최대 3, 자기 계정 제외."""
    deck_path = out_dir / "deck.json"
    deck = json.loads(deck_path.read_text(encoding="utf-8")) if deck_path.exists() else {}
    raw = deck.get("collaborators") or config.CHANNEL_CONFIG.get("collaborators") or []
    own = str(config.CHANNEL_CONFIG.get("ig_username") or "").lower()
    names = []
    for name in raw if isinstance(raw, list) else [raw]:
        name = str(name).strip().lstrip("@")
        if USERNAME_RE.match(name) and name.lower() != own and name.lower() not in [n.lower() for n in names]:
            names.append(name)
    return names[:3]


def our_channels() -> dict[str, str]:
    """인스타 사용자명 → 우리 채널 키(콜라보 초대 자동 수락 대상)."""
    out = {}
    for path in (Path(__file__).resolve().parent / "channels").glob("*/channel.json"):
        name = json.loads(path.read_text(encoding="utf-8")).get("ig_username")
        if name:
            out[name.lower()] = path.parent.name
    return out


def news_for(item: dict) -> dict | None:
    if not item.get("news_item_id"):
        return None
    rows = (get_client().table("news_items")
            .select("id,title,source,url,official_url,apply_start,apply_end,listing,source_type")
            .eq("id", item["news_item_id"]).execute().data or [])
    return rows[0] if rows else None


def rebuild_reel(out_dir: Path) -> bool:
    """캡션 D-n이 바뀌었을 때 릴스 도입부(D-n)도 맞춘다. 실패하면 깨진 영상을 지우고 캐러셀만 올린다."""
    import reel  # ffmpeg·Pillow 의존이라 필요할 때만
    try:
        deck = json.loads((out_dir / "deck.json").read_text(encoding="utf-8"))
        path = reel.make_reel(out_dir, config.CHANNEL_DIR / "music", deck)
        seconds = reel.duration(path)
        if path.stat().st_size < 10_000 or not reel.MIN_SEC <= seconds <= reel.MAX_SEC:
            raise RuntimeError(f"릴스 길이 {seconds:.1f}초 / {path.stat().st_size}바이트")
        return True
    except Exception as e:
        (out_dir / "reel.mp4").unlink(missing_ok=True)
        log.warning("릴스 다시 만들기 실패(캐러셀만 게시): %s", e)
        events.record("reel", "failed", message=f"D-n 갱신 후 릴스 다시 만들기 실패: {e}")
        return False


def already_posted_reason(item: dict, news: dict | None) -> str | None:
    """전날 제작 카드는 만든 뒤 하루 이상 기다리므로, 그 사이 같은 공고·같은 주제가 올라갔는지 게시 직전에 다시 본다(최근 7일 게시분)."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = (get_client().table("cards").select("id,news_item_id").eq("published", True)
            .in_("category", list(config.CATEGORIES)).gte("published_at", since).neq("id", item["id"]).execute().data or [])
    ids = [r["news_item_id"] for r in rows if r.get("news_item_id")]
    if item.get("news_item_id") in ids:
        return "같은 공고·기사를 이미 게시함"
    if not news or not ids:
        return None
    history = get_client().table("news_items").select("id,title,url").in_("id", ids).execute().data or []
    if news.get("source_type") == "listing":  # 공고는 기관만 다른 비슷한 제목이 많아 URL과 높은 유사도만 본다
        from rapidfuzz import fuzz
        for prev in history:
            if news.get("url") and news["url"] == prev.get("url"):
                return f"같은 공고 URL(이전: {prev['title'][:30]})"
            if fuzz.token_sort_ratio(news.get("title") or "", prev["title"]) >= 90:
                return f"거의 같은 공고 제목(이전: {prev['title'][:30]})"
        return None
    import auto_select
    return auto_select.duplicate_reason({"id": news.get("id"), "title": news.get("title") or "", "url": news.get("url")}, history)


def prepare(item: dict, dry_run: bool = False) -> dict | None:
    """게시 직전 준비: 마감 확인, 캡션 D-n 다시 계산(+릴스), 댓글 자료 안내 줄, 공동 작업자. 못 올리면 None."""
    out_dir = Path(item["image_path"])
    caption_path = out_dir / "caption.txt"
    caption = caption_path.read_text(encoding="utf-8") if caption_path.exists() else item["final_title"]
    news = news_for(item)
    plan = {"out_dir": out_dir, "pngs": sorted(out_dir.glob("[0-9][0-9].png")), "news": news, "notes": []}
    if schedule_plan.enabled() and (dup := already_posted_reason(item, news)):
        reason = f"{dup}이라 게시하지 않음"
        if dry_run:
            return {**plan, "notes": [reason], "caption": caption, "collaborators": []}
        get_client().table("cards").update({"approval": "rejected", "approved_by": "system:duplicate"}).eq("id", item["id"]).execute()
        events.record("publish", "skipped", category=item["category"], card_id=item["id"], message=reason)
        telegram_client.send_message(f"⏭ 승인했지만 {reason}\n{item['final_title']}")
        return None
    if news and news.get("apply_end"):
        left = (datetime.fromisoformat(news["apply_end"]).date() - datetime.now(KST).date()).days
        if left < LISTING_MIN_DAYS_LEFT:
            reason = f"마감까지 {left}일이라 게시하지 않음(3일 이상 남아야 함)"
            if dry_run:
                return {**plan, "notes": [reason], "caption": caption, "collaborators": []}
            get_client().table("cards").update({"approval": "rejected", "approved_by": "system:deadline"}).eq("id", item["id"]).execute()
            events.record("publish", "skipped", category=item["category"], card_id=item["id"], message=reason)
            telegram_client.send_message(f"⏭ 승인했지만 {reason}\n{item['final_title']}")
            return None
        updated = re.sub(r"D-\d+", f"D-{left}", caption)
        if updated != caption:
            old = sorted(set(re.findall(r"D-\d+", caption)))
            plan["notes"].append(f"캡션 {'/'.join(old)} → D-{left}" + (" · 릴스 다시 만듦" if config.CHANNEL_CONFIG.get("reels") else ""))
            if not dry_run:
                caption_path.write_text(updated, encoding="utf-8")
                if config.CHANNEL_CONFIG.get("reels"):
                    rebuild_reel(out_dir)
            caption = updated
    with_dm = comment_dm.add_line(caption)
    if with_dm != caption:
        if len(with_dm) <= comment_dm.CAPTION_HARD_MAX:
            caption = with_dm
            plan["notes"].append("댓글 자료 안내 줄 추가")
        else:
            plan["notes"].append("댓글 자료 안내 줄을 넣으면 2200자를 넘어 뺌")
    plan["caption"] = caption
    plan["collaborators"] = collaborators_for(out_dir)
    return plan


def accept_collab_invites(collaborators: list[str], media_ids: list[str]) -> None:
    """공동 작업자가 우리 다른 채널이면 그 채널 토큰으로 초대를 수락한다.
    별도 프로세스로 띄우고 계정 값(토큰 등)은 물려주지 않는다(물려주면 그 채널 config가 이 채널 토큰을 그대로 씀)."""
    channels = our_channels()
    for name in collaborators:
        channel = channels.get(name.lower())
        if not channel:
            continue
        env = {k: v for k, v in os.environ.items() if k not in ACCOUNT_KEYS}
        env["CHANNEL"] = channel
        for media_id in media_ids:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "collab_invites.py"),
                                     "--media", str(media_id), "--wait", "120"],
                                    env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
            if result.returncode != 0:
                log.warning("@%s 공동 작업 초대 수락 못 함(%s): %s", name, media_id, (result.stderr or result.stdout)[-300:])
                telegram_client.send_message(f"⚠️ @{name} 공동 작업 초대를 자동 수락하지 못함(게시는 됨)\nmedia {media_id}\n앱에서 직접 수락해줘")


def handle_regenerations() -> None:
    import generate_cardset  # 모델·렌더 의존이라 필요할 때만
    for card in fetch_regenerate_requests():
        try:
            generate_cardset.regenerate(card)
        except Exception as e:
            log.error("card %s 다시 만들기 실패: %s", card["id"], e)
            get_client().table("cards").update({"approval": "pending"}).eq("id", card["id"]).execute()
            events.record("cardset", "failed", category=card["category"], card_id=card["id"], message=f"다시 만들기 실패: {e}")


def print_dry_run(card_id: int | None, approved_only: bool) -> None:
    if approved_only:
        regen = fetch_regenerate_requests()
        print(f"다시 만들기 요청 {len(regen)}건 (dry-run이라 만들지 않음)")
        for card in regen:
            print(f"  card {card['id']} · news_item {card['news_item_id']} · {card['final_title']}")
    items = fetch_unpublished(card_id)
    print(f"게시 대상 {len(items)}건 (채널 {config.CHANNEL}{', 승인된 카드만' if requires_approval() else ''})")
    if items and approved_only and card_id is None and schedule_plan.enabled():
        items, reason = schedule_plan.choose(items)
        print(f"전날 제작: 이번 게시 시각 후보 {[i['id'] for i in items]}{f' ({reason})' if reason else ''}")
    for item in items:
        print(f"  card {item['id']} · news_item {item['news_item_id']} · {item['category']} · {item['image_path']} · {item['final_title']}")
        plan = prepare(item, dry_run=True)
        print(f"    이미지 {len(plan['pngs'])}장 · 공동 작업자 {', '.join(plan['collaborators']) or '없음'} · "
              f"{' / '.join(plan['notes']) or '캡션 그대로'}")
    try:
        slots, reason = posting_slots()
        print(f"이번 실행 게시 가능 {slots}건{f' ({reason})' if reason else ''}")
    except Exception as e:
        print(f"게시 이력 조회 실패: {e}")


def main(argv: list[str] | None = None):
    card_id, dry_run, approved_only = parse_args(sys.argv[1:] if argv is None else argv)
    if approved_only and not requires_approval():
        log.info("이 채널(%s)은 승인 절차가 없어 --approved-only를 쓰지 않음", config.CHANNEL)
        return
    if dry_run:
        print_dry_run(card_id, approved_only)
        return
    if not config.INSTAGRAM_ACCESS_TOKEN or not config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        log.warning("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID 미설정, 건너뜀")
        events.record("publish", "skipped", message="인스타그램 토큰/계정 ID 미설정")
        return
    # 10분마다 도는 발행 작업에서 댓글 DM(리틀리) 미설정 게시물을 확인해 항목당 한 번 알린다
    comment_dm.alert_pending(telegram_client.send_message)

    try:
        instagram_client.verify_token()
    except Exception as e:
        log.error("인스타그램 토큰 확인 실패, 업로드 전체 건너뜀: %s", e)
        events.record("publish", "failed", message=f"토큰 확인 실패: {e}")
        telegram_client.send_message(
            f"⚠️ 인스타그램 토큰이 유효하지 않습니다(카드 생성은 정상, 업로드만 건너뜀)\n{e}\n"
            "액세스 토큰을 재발급해 .env의 INSTAGRAM_ACCESS_TOKEN을 갱신해주세요."
        )
        return

    if story_queue.enabled():  # 지난 게시물의 Aside 스토리 공유 결과 확인(게시할 카드가 없어도)
        story_queue.check()
    if approved_only:  # [다시 만들기] 요청 처리(새 카드는 다시 승인 요청을 보낸다)
        handle_regenerations()

    client = get_client()
    items = fetch_unpublished(card_id)
    log.info("업로드 대상: %d건%s", len(items), f" (card {card_id}만)" if card_id is not None else "")
    if items and approved_only and card_id is None and schedule_plan.enabled():
        # 전날 제작: 게시 시각마다 1개. 그 시각 카드 → 늦게 승인된 앞 시각 카드 → 예비분 순서
        items, reason = schedule_plan.choose(items)
        if not items:
            log.info("게시 건너뜀(전날 제작): %s", reason)
            return
        log.info("이번 게시 시각 후보: %s", ", ".join(
            f"card {i['id']}" + ("(예비분)" if i.get("reserve") else "") for i in items))
    if not items:
        if not approved_only:  # 10분 작업은 대부분 빈 실행이라 기록하지 않는다(대시보드 타임라인이 덮이지 않게)
            events.record("publish", "skipped", count=0, message="게시할 카드세트 없음")
        return
    try:
        slots, reason = posting_slots()
    except Exception as e:
        slots, reason = 0, f"게시 이력 조회 실패({e})라 몰아 게시를 막으려고 건너뜀"
    if not slots:
        log.info("게시 건너뜀: %s (남은 카드 %d건)", reason, len(items))
        if not approved_only:
            events.record("publish", "skipped", count=len(items), message=f"게시 건너뜀: {reason}")
        return

    posted = 0
    for item in items:
        if posted >= slots:
            break
        plan = prepare(item)
        if not plan:
            continue
        out_dir, pngs, caption, collaborators = plan["out_dir"], plan["pngs"], plan["caption"], plan["collaborators"]

        if not pngs:
            log.warning("[%s] 카드 이미지 없음, 건너뜀: %s", item["category"], out_dir)
            events.record("publish", "failed", category=item["category"], card_id=item["id"], message="카드 이미지 없음")
            continue

        from publish_guard import claim_card
        if not claim_card(client, item["id"], requires_approval()):
            log.warning("card %s already claimed; skipping", item["id"])
            continue
        events.record(
            "publish", "started", category=item["category"], card_id=item["id"], count=len(pngs),
            message=f"캐러셀 {len(pngs)}장 업로드 시작" + (f" · 공동 작업자 {', '.join(collaborators)}" if collaborators else ""),
            detail={"notes": plan["notes"]} if plan["notes"] else None,
        )

        try:
            media_id = instagram_client.upload_and_publish_carousel([str(p) for p in pngs], caption, collaborators)
        except Exception as e:
            log.error("[%s] 인스타그램 업로드 실패: %s", item["category"], e)
            events.record("publish", "failed", category=item["category"], card_id=item["id"], message=str(e))
            telegram_client.send_message(
                f"⚠️ 인스타그램 업로드 실패\n{item['final_title']}\n{e}"
            )
            continue
        receipt = {"card_id": item["id"], "media_id": str(media_id), "run_id": events.RUN_ID,
                   "created_at": datetime.now(timezone.utc).isoformat()}
        (out_dir / "publish-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        posted += 1

        client.table("cards").update({"published": True, "publish_status": "published", "media_id": str(media_id),
                                      "published_at": datetime.now(timezone.utc).isoformat()}).eq("id", item["id"]).execute()
        log.info("[%s] 캐러셀 업로드 완료(%d장) media_id=%s", item["category"], len(pngs), media_id)
        events.record(
            "publish", "success", category=item["category"], card_id=item["id"], count=len(pngs),
            message="인스타그램 게시 완료", detail={"media_id": media_id, "collaborators": collaborators or None},
        )
        # 스토리 공유는 Aside 루틴(12:30·19:30)이 이 목록을 보고 한다(디징 story_queue)
        if story_queue.enabled():
            story_queue.add(media_id, item["final_title"], item["id"])
        # 댓글 → 자료 DM용 자료 목록(Aside가 노션 페이지를 만들고 notion_url을 채움)
        if comment_dm.enabled():
            deck_path = out_dir / "deck.json"
            notes = json.loads(deck_path.read_text(encoding="utf-8")).get("guide_notes") if deck_path.exists() else None
            comment_dm.add_guide(comment_dm.guide_entry(media_id, instagram_client.permalink(media_id),
                                                        item["final_title"], caption, plan["news"], notes))
        reel_id = publish_reel(item, out_dir / "reel.mp4", caption, collaborators)
        if collaborators:
            accept_collab_invites(collaborators, [m for m in (media_id, reel_id) if m])
        # 파이프라인이 직접 Business Suite 화면으로 공유(share_post_to_story). 헤드리스는 로그인이 풀려 디징은 끔
        if config.CHANNEL_CONFIG.get("share_post_to_story"):
            share_to_story.share(media_id, card_id=item["id"], category=item["category"])


def publish_reel(item: dict, video: Path, caption: str, collaborators: list[str] | None = None) -> str | None:
    """캐러셀과 같은 카드로 만든 릴스를 이어서 올린다(채널 설정 reels). 실패해도 캐러셀은 이미 올라갔으므로
    알림만 보내고, 다음 실행에서 다시 올리지 않는다(같은 내용이 늦게 두 번 올라가는 것 방지)."""
    if not config.CHANNEL_CONFIG.get("reels"):
        return None
    if not video.exists():
        events.record("reel", "skipped", category=item["category"], card_id=item["id"], message="릴스 영상 없음")
        return None
    try:
        media_id = instagram_client.upload_and_publish_reel(str(video), caption, collaborators=collaborators)
    except Exception as e:
        log.error("[%s] 릴스 업로드 실패: %s", item["category"], e)
        events.record("reel", "failed", category=item["category"], card_id=item["id"], message=str(e))
        telegram_client.send_message(f"⚠️ 릴스 업로드 실패(캐러셀은 게시됨)\n{item['final_title']}\n{e}")
        return None
    log.info("[%s] 릴스 업로드 완료 media_id=%s", item["category"], media_id)
    events.record("reel", "success", category=item["category"], card_id=item["id"], message="릴스 게시 완료",
                  detail={"media_id": media_id})
    return media_id


if __name__ == "__main__":
    main()

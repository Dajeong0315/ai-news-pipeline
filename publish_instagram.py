"""오늘 만든 카드세트(cards.published=false)를 캐러셀로 인스타그램에 업로드한다.

오늘(KST) 만든 카드만 올린다. 날짜 제한이 없으면 테스트나 실패 후 남은 지난 카드가
다음 정기 실행 때 한꺼번에 게시된다(2026-09-03 미발행 9장 동시 게시 사고와 같은 경로).

cards.image_path는 generate_cardset.py가 만든 폴더 경로(01.png~NN.png +
caption.txt가 들어있는)를 가리킨다. 폴더 하나 = 인스타그램 캐러셀 게시물 하나.

실행:
    python publish_instagram.py
    python publish_instagram.py --card-id 62 --dry-run   # 카드 하나만, 게시 대상만 출력
    python publish_instagram.py --card-id 62             # 카드 하나만 게시
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import events
import instagram_client
import share_to_story
import story_queue
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_instagram")


KST = timezone(timedelta(hours=9))


def fetch_unpublished(card_id: int | None = None) -> list[dict]:
    client = get_client()
    today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    query = (
        client.table("cards")
        .select("id,news_item_id,category,final_title,image_path,publish_order")
        .eq("published", False)
        .in_("category", list(config.CATEGORIES))
        .gte("created_at", today_start.isoformat())
    )
    if card_id is not None:  # 운영자가 검토한 카드 하나만(2026-11-01 디징 첫 게시: 같은 날 만든 다른 카드가 올라가지 않게)
        query = query.eq("id", card_id)
    return query.order("publish_order").execute().data or []


def parse_args(argv: list[str]) -> tuple[int | None, bool]:
    """--card-id N: 그 카드만 올린다. --dry-run: 게시 대상만 출력하고 끝낸다."""
    card_id = int(argv[argv.index("--card-id") + 1]) if "--card-id" in argv else None
    return card_id, "--dry-run" in argv


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


def main(argv: list[str] | None = None):
    card_id, dry_run = parse_args(sys.argv[1:] if argv is None else argv)
    if dry_run:
        items = fetch_unpublished(card_id)
        print(f"게시 대상 {len(items)}건 (채널 {config.CHANNEL})")
        for item in items:
            print(f"  card {item['id']} · news_item {item['news_item_id']} · {item['category']} · {item['image_path']} · {item['final_title']}")
        return
    if not config.INSTAGRAM_ACCESS_TOKEN or not config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        log.warning("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID 미설정, 건너뜀")
        events.record("publish", "skipped", message="인스타그램 토큰/계정 ID 미설정")
        return

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

    client = get_client()
    items = fetch_unpublished(card_id)
    log.info("업로드 대상: %d건%s", len(items), f" (card {card_id}만)" if card_id is not None else "")
    if not items:
        events.record("publish", "skipped", count=0, message="게시할 카드세트 없음")
        return
    try:
        slots, reason = posting_slots()
    except Exception as e:
        slots, reason = 0, f"게시 이력 조회 실패({e})라 몰아 게시를 막으려고 건너뜀"
    if not slots:
        log.info("게시 건너뜀: %s (남은 카드 %d건)", reason, len(items))
        events.record("publish", "skipped", count=len(items), message=f"게시 건너뜀: {reason}")
        return
    items = items[:slots]

    for item in items:
        out_dir = Path(item["image_path"])
        pngs = sorted(out_dir.glob("[0-9][0-9].png"))
        caption_path = out_dir / "caption.txt"
        caption = caption_path.read_text(encoding="utf-8") if caption_path.exists() else item["final_title"]

        if not pngs:
            log.warning("[%s] 카드 이미지 없음, 건너뜀: %s", item["category"], out_dir)
            events.record("publish", "failed", category=item["category"], card_id=item["id"], message="카드 이미지 없음")
            continue

        events.record(
            "publish", "started", category=item["category"], card_id=item["id"], count=len(pngs),
            message=f"캐러셀 {len(pngs)}장 업로드 시작",
        )

        try:
            media_id = instagram_client.upload_and_publish_carousel([str(p) for p in pngs], caption)
        except Exception as e:
            log.error("[%s] 인스타그램 업로드 실패: %s", item["category"], e)
            events.record("publish", "failed", category=item["category"], card_id=item["id"], message=str(e))
            telegram_client.send_message(
                f"⚠️ 인스타그램 업로드 실패\n{item['final_title']}\n{e}"
            )
            continue

        client.table("cards").update({"published": True}).eq("id", item["id"]).execute()
        log.info("[%s] 캐러셀 업로드 완료(%d장) media_id=%s", item["category"], len(pngs), media_id)
        events.record(
            "publish", "success", category=item["category"], card_id=item["id"], count=len(pngs),
            message="인스타그램 게시 완료", detail={"media_id": media_id},
        )
        # 스토리 공유는 Aside 루틴(12:30·19:30)이 이 목록을 보고 한다(디징 story_queue)
        if story_queue.enabled():
            story_queue.add(media_id, item["final_title"], item["id"])
        publish_reel(item, out_dir / "reel.mp4", caption)
        # 파이프라인이 직접 Business Suite 화면으로 공유(share_post_to_story). 헤드리스는 로그인이 풀려 디징은 끔
        if config.CHANNEL_CONFIG.get("share_post_to_story"):
            share_to_story.share(media_id, card_id=item["id"], category=item["category"])


def publish_reel(item: dict, video: Path, caption: str) -> None:
    """캐러셀과 같은 카드로 만든 릴스를 이어서 올린다(채널 설정 reels). 실패해도 캐러셀은 이미 올라갔으므로
    알림만 보내고, 다음 실행에서 다시 올리지 않는다(같은 내용이 늦게 두 번 올라가는 것 방지)."""
    if not config.CHANNEL_CONFIG.get("reels"):
        return
    if not video.exists():
        events.record("reel", "skipped", category=item["category"], card_id=item["id"], message="릴스 영상 없음")
        return
    try:
        media_id = instagram_client.upload_and_publish_reel(str(video), caption)
    except Exception as e:
        log.error("[%s] 릴스 업로드 실패: %s", item["category"], e)
        events.record("reel", "failed", category=item["category"], card_id=item["id"], message=str(e))
        telegram_client.send_message(f"⚠️ 릴스 업로드 실패(캐러셀은 게시됨)\n{item['final_title']}\n{e}")
        return
    log.info("[%s] 릴스 업로드 완료 media_id=%s", item["category"], media_id)
    events.record("reel", "success", category=item["category"], card_id=item["id"], message="릴스 게시 완료",
                  detail={"media_id": media_id})


if __name__ == "__main__":
    main()

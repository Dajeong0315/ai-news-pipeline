"""오늘 만든 카드세트(cards.published=false)를 캐러셀로 인스타그램에 업로드한다.

오늘(KST) 만든 카드만 올린다. 날짜 제한이 없으면 테스트나 실패 후 남은 지난 카드가
다음 정기 실행 때 한꺼번에 게시된다(2026-09-03 미발행 9장 동시 게시 사고와 같은 경로).

cards.image_path는 generate_cardset.py가 만든 폴더 경로(01.png~NN.png +
caption.txt가 들어있는)를 가리킨다. 폴더 하나 = 인스타그램 캐러셀 게시물 하나.

실행:
    python publish_instagram.py
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import events
import instagram_client
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_instagram")


KST = timezone(timedelta(hours=9))


def fetch_unpublished() -> list[dict]:
    client = get_client()
    today_start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    resp = (
        client.table("cards")
        .select("id,category,final_title,image_path,publish_order")
        .eq("published", False)
        .gte("created_at", today_start.isoformat())
        .order("publish_order")
        .execute()
    )
    return resp.data or []


def main():
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

    client = get_client()
    items = fetch_unpublished()
    log.info("업로드 대상: %d건", len(items))
    if not items:
        events.record("publish", "skipped", count=0, message="게시할 카드세트 없음")

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


if __name__ == "__main__":
    main()

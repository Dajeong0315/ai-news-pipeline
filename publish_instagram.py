"""오늘 만든 카드세트(cards.published=false)를 캐러셀로 인스타그램에 업로드한다.

cards.image_path는 generate_cardset.py가 만든 폴더 경로(01.png~NN.png +
caption.txt가 들어있는)를 가리킨다. 폴더 하나 = 인스타그램 캐러셀 게시물 하나.

실행:
    python publish_instagram.py
"""

import logging
from pathlib import Path

import config
import instagram_client
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_instagram")


def fetch_unpublished() -> list[dict]:
    client = get_client()
    resp = (
        client.table("cards")
        .select("id,category,final_title,image_path,publish_order")
        .eq("published", False)
        .order("publish_order")
        .execute()
    )
    return resp.data or []


def main():
    if not config.INSTAGRAM_ACCESS_TOKEN or not config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        log.warning("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID 미설정, 건너뜀")
        return

    try:
        instagram_client.verify_token()
    except Exception as e:
        log.error("인스타그램 토큰 확인 실패, 업로드 전체 건너뜀: %s", e)
        telegram_client.send_message(
            f"⚠️ 인스타그램 토큰이 유효하지 않습니다(카드 생성은 정상, 업로드만 건너뜀)\n{e}\n"
            "액세스 토큰을 재발급해 .env의 INSTAGRAM_ACCESS_TOKEN을 갱신해주세요."
        )
        return

    client = get_client()
    items = fetch_unpublished()
    log.info("업로드 대상: %d건", len(items))

    for item in items:
        out_dir = Path(item["image_path"])
        pngs = sorted(out_dir.glob("[0-9][0-9].png"))
        caption_path = out_dir / "caption.txt"
        caption = caption_path.read_text(encoding="utf-8") if caption_path.exists() else item["final_title"]

        if not pngs:
            log.warning("[%s] 카드 이미지 없음, 건너뜀: %s", item["category"], out_dir)
            continue

        try:
            media_id = instagram_client.upload_and_publish_carousel([str(p) for p in pngs], caption)
        except Exception as e:
            log.error("[%s] 인스타그램 업로드 실패: %s", item["category"], e)
            telegram_client.send_message(
                f"⚠️ 인스타그램 업로드 실패\n{item['final_title']}\n{e}"
            )
            continue

        client.table("cards").update({"published": True}).eq("id", item["id"]).execute()
        log.info("[%s] 캐러셀 업로드 완료(%d장) media_id=%s", item["category"], len(pngs), media_id)


if __name__ == "__main__":
    main()

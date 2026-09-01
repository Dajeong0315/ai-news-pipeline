"""오늘 만든 카드(cards.published=false)를 발행 순서대로 인스타그램에 업로드한다.

실행:
    python publish_instagram.py
"""

import logging

import config
import instagram_client
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("publish_instagram")

HASHTAGS_BY_CATEGORY = {
    "index_macro": "#코스피 #코스닥 #증시 #환율",
    "stock": "#주식 #종목분석 #기업뉴스",
    "policy_industry": "#정책 #산업동향 #한국은행",
}


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


def build_caption(category: str, final_title: str) -> str:
    label = config.CATEGORY_LABELS.get(category, category)
    hashtags = HASHTAGS_BY_CATEGORY.get(category, "")
    return f"{final_title}\n\n[{label}]\n{hashtags} #카드뉴스 #경제 #자동화"


def main():
    if not config.INSTAGRAM_ACCESS_TOKEN or not config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        log.warning("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID 미설정, 건너뜀")
        return

    client = get_client()
    items = fetch_unpublished()
    log.info("업로드 대상: %d건", len(items))

    for item in items:
        try:
            caption = build_caption(item["category"], item["final_title"])
            media_id = instagram_client.upload_and_publish(item["image_path"], caption)
        except Exception as e:
            log.error("[%s] 인스타그램 업로드 실패: %s", item["category"], e)
            telegram_client.send_message(
                f"⚠️ 인스타그램 업로드 실패\n{item['final_title']}\n{e}"
            )
            continue

        client.table("cards").update({"published": True}).eq("id", item["id"]).execute()
        log.info("[%s] 업로드 완료 media_id=%s", item["category"], media_id)


if __name__ == "__main__":
    main()

"""정제된 뉴스(status='filtered') 중 카테고리별 상위 후보를 텔레그램으로 전송한다.

승인/거절은 텔레그램 인라인 버튼 클릭 -> webhook/api/index.py 가 처리한다.
이 스크립트는 전송과 approval_requests 기록, news_items.status 갱신까지만 담당한다.

실행:
    python send_candidates.py
"""

import logging

import config
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("send_candidates")


def fetch_candidates(category: str) -> list[dict]:
    client = get_client()
    resp = (
        client.table("news_items")
        .select("id,title,source,url,category,published_at")
        .eq("category", category)
        .eq("status", "filtered")
        .order("published_at", desc=True)
        .limit(config.CANDIDATES_PER_CATEGORY)
        .execute()
    )
    return resp.data or []


def format_message(item: dict) -> str:
    label = config.CATEGORY_LABELS.get(item["category"], item["category"])
    return (
        f"[{label}]\n"
        f"<b>{item['title']}</b>\n"
        f"출처: {item.get('source') or '알 수 없음'}\n"
        f"{item['url']}"
    )


def send_category(category: str) -> int:
    client = get_client()
    items = fetch_candidates(category)
    if not items:
        log.info("[%s] 전송할 후보 없음", category)
        return 0

    label = config.CATEGORY_LABELS.get(category, category)
    telegram_client.send_message(
        f"📌 [{label}] 오늘의 후보 {len(items)}건 — 카테고리당 <b>1건만</b> 승인해주세요."
    )

    sent = 0
    for item in items:
        text = format_message(item)
        keyboard = telegram_client.approval_keyboard(item["id"])
        result = telegram_client.send_message(text, reply_markup=keyboard)
        message_id = result.get("result", {}).get("message_id")

        client.table("approval_requests").insert(
            {
                "news_item_id": item["id"],
                "telegram_message_id": message_id,
                "decision": "pending",
            }
        ).execute()
        client.table("news_items").update({"status": "pending_approval"}).eq(
            "id", item["id"]
        ).execute()
        sent += 1

    return sent


def main():
    total = 0
    for category in config.CATEGORIES:
        total += send_category(category)
    log.info("총 %d건 전송 완료", total)


if __name__ == "__main__":
    main()

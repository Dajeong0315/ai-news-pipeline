"""오늘 만들어진 카드세트 수가 비정상적으로 많으면(재시도 폭주 등) 텔레그램 경고를 보낸다.

Cloudflare Workers AI는 실시간 잔여 뉴런을 조회하는 간단한 API가 없어(대시보드
에서만 확인 가능), cards 테이블에 오늘 쌓인 행 수를 대리 지표로 쓴다. 카드
1건당 LLM 호출은 최대 generate_cardset.MAX_ATTEMPTS(3)회까지 걸릴 수 있어
평소보다 훨씬 많은 카드가 쌓였다면 재시도 루프 등 이상 상황일 가능성이 크다.

실행:
    python check_usage.py
"""

import logging
from datetime import datetime, timedelta, timezone

import config
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("check_usage")

KST = timezone(timedelta(hours=9))


def today_start_utc_iso() -> str:
    now_kst = datetime.now(KST)
    start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_kst.astimezone(timezone.utc).isoformat()


def main():
    client = get_client()
    start = today_start_utc_iso()
    card_count = client.table("cards").select("id", count="exact").gte("created_at", start).execute().count or 0

    log.info("오늘 생성된 카드세트: %d건 (안전선 %d)", card_count, config.SAFE_DAILY_CLOUDFLARE_CALLS)

    if card_count > config.SAFE_DAILY_CLOUDFLARE_CALLS:
        telegram_client.send_message(
            f"⚠️ 오늘 생성된 카드세트가 {card_count}건으로 평소(3~6건)보다 많습니다. "
            f"재시도 루프 등 이상 여부를 확인해주세요."
        )


if __name__ == "__main__":
    main()

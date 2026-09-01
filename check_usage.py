"""오늘 Cloudflare Workers AI 호출량을 추정해 비정상적으로 많으면 텔레그램 경고를 보낸다.

Cloudflare는 실시간 잔여 뉴런을 간단히 조회하는 API가 없어(대시보드에서만
확인 가능), 우리가 직접 남긴 레코드 수(텍스트 프롬프트 변환/FLUX 생성/비전
검증)를 오늘 호출량의 대리 지표로 사용한다. 평소 하루 사용량은 9회 안팎
(3카테고리 x 텍스트+FLUX+비전)이므로, config.SAFE_DAILY_CLOUDFLARE_CALLS
(기본 30)를 넘으면 재시도 폭주 등 이상 상황으로 보고 경고한다.

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


def count_since(table: str, column: str, start: str, extra_filter=None) -> int:
    client = get_client()
    query = client.table(table).select("id", count="exact").gte(column, start)
    if extra_filter:
        query = extra_filter(query)
    return query.execute().count or 0


def main():
    start = today_start_utc_iso()

    text_calls = count_since("image_prompts", "created_at", start)
    flux_calls = count_since("generated_images", "created_at", start)
    vision_calls = count_since(
        "generated_images",
        "created_at",
        start,
        extra_filter=lambda q: q.not_.is_("vision_check_passed", "null"),
    )
    total = text_calls + flux_calls + vision_calls

    log.info(
        "오늘 Cloudflare 호출 추정: 텍스트 %d + FLUX %d + 비전 %d = %d (안전선 %d)",
        text_calls, flux_calls, vision_calls, total, config.SAFE_DAILY_CLOUDFLARE_CALLS,
    )

    if total > config.SAFE_DAILY_CLOUDFLARE_CALLS:
        telegram_client.send_message(
            f"⚠️ 오늘 Cloudflare Workers AI 호출 추정치가 {total}회로 평소(9회 안팎)보다 "
            f"많습니다(텍스트 {text_calls} + FLUX {flux_calls} + 비전 {vision_calls}). "
            f"재시도 루프 등 이상 여부를 확인해주세요."
        )


if __name__ == "__main__":
    main()

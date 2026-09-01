"""검색어(keyword)별 수집 대비 승인 비율을 집계해 리포트한다 (운영자가 수동/주기 실행).

keywords.json 튜닝(성과 낮은 검색어 교체 등)은 운영자의 판단 영역이라 자동으로
적용하지 않고, 리포트만 만들어 콘솔과 텔레그램으로 보여준다.

실행:
    python keyword_stats.py
"""

import logging
from collections import defaultdict

import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("keyword_stats")

PAGE_SIZE = 1000


def fetch_all_items() -> list[dict]:
    client = get_client()
    rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            client.table("news_items")
            .select("keyword,category,status")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    rows = fetch_all_items()
    stats = defaultdict(lambda: {"collected": 0, "approved": 0, "category": ""})
    for r in rows:
        kw = r.get("keyword") or "(알수없음)"
        stats[kw]["collected"] += 1
        stats[kw]["category"] = r.get("category", "")
        if r.get("status") == "approved":
            stats[kw]["approved"] += 1

    ranked = sorted(
        stats.items(), key=lambda kv: kv[1]["approved"] / max(kv[1]["collected"], 1), reverse=True
    )

    lines = ["📊 검색어별 승인률 (전체 기간, 상위 15개)"]
    for kw, s in ranked[:15]:
        rate = s["approved"] / max(s["collected"], 1) * 100
        line = f"[{s['category']}] {kw}: {s['approved']}/{s['collected']} ({rate:.1f}%)"
        lines.append(line)
        log.info(line)

    zero_approval = [kw for kw, s in stats.items() if s["approved"] == 0 and s["collected"] >= 20]
    if zero_approval:
        lines.append(f"\n승인 0건(수집 20건 이상) 검색어 — 교체 검토 대상: {', '.join(zero_approval)}")
        log.info("승인 0건 검색어: %s", zero_approval)

    telegram_client.send_message("\n".join(lines))


if __name__ == "__main__":
    main()

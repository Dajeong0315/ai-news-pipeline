"""검색어(keyword)별 수집 대비 승인 비율을 집계해 리포트한다 (운영자가 수동/주기 실행).

keywords.json 튜닝(성과 낮은 검색어 교체 등)은 운영자의 판단 영역이라 자동으로
적용하지 않고, 리포트만 만들어 콘솔과 텔레그램으로 보여준다.

auto 모드에서는 자동 선정(auto_select)도 status='approved'로 남기므로 "승인"은
곧 "선정"이다. run_daily.py auto가 끝날 때 snapshot()이 같은 집계를 최근 30일
기준으로 pipeline_events(stage='keyword_stats')에 남겨 대시보드가 읽는다.

실행:
    python keyword_stats.py
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import events
import telegram_client
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("keyword_stats")

PAGE_SIZE = 1000
ZERO_APPROVAL_MIN_COLLECTED = 20
SNAPSHOT_DAYS = 30


def fetch_all_items(since: str | None = None) -> list[dict]:
    client = get_client()
    rows: list[dict] = []
    offset = 0
    while True:
        query = client.table("news_items").select("keyword,category,status")
        if since:
            query = query.gte("collected_at", since)
        batch = query.order("id").range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def aggregate(rows: list[dict]) -> dict:
    stats = defaultdict(lambda: {"collected": 0, "approved": 0, "category": ""})
    for r in rows:
        kw = r.get("keyword") or "(알수없음)"
        stats[kw]["collected"] += 1
        stats[kw]["category"] = r.get("category", "")
        if r.get("status") == "approved":
            stats[kw]["approved"] += 1
    return stats


def is_replacement_candidate(s: dict) -> bool:
    return s["approved"] == 0 and s["collected"] >= ZERO_APPROVAL_MIN_COLLECTED


def snapshot(days: int = SNAPSHOT_DAYS) -> None:
    """대시보드 [키워드 성과] 탭용 집계를 pipeline_events에 남긴다.
    keywords.json에 있지만 기간 내 한 건도 수집되지 않은 검색어도 0건으로 포함한다."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    stats = aggregate(fetch_all_items(since))
    with open("keywords.json", "r", encoding="utf-8") as f:
        configured = {cat: kws for cat, kws in json.load(f).items() if not cat.startswith("_")}
    for category, keywords in configured.items():
        for kw in keywords:
            if kw not in stats:
                stats[kw] = {"collected": 0, "approved": 0, "category": category}

    in_config = {kw for kws in configured.values() for kw in kws}
    rows = [
        {
            "keyword": kw, "category": s["category"], "collected": s["collected"], "approved": s["approved"],
            "replace": is_replacement_candidate(s) or (s["collected"] == 0 and kw in in_config),
            "in_config": kw in in_config,
        }
        for kw, s in stats.items()
    ]
    flagged = sum(r["replace"] for r in rows)
    events.record(
        "keyword_stats", "success", count=len(rows),
        message=f"검색어 {len(rows)}개 집계 (최근 {days}일), 교체 검토 {flagged}개",
        detail={"days": days, "min_collected": ZERO_APPROVAL_MIN_COLLECTED, "rows": rows},
    )


def main():
    stats = aggregate(fetch_all_items())

    ranked = sorted(
        stats.items(), key=lambda kv: kv[1]["approved"] / max(kv[1]["collected"], 1), reverse=True
    )

    lines = ["📊 검색어별 승인률 (전체 기간, 상위 15개)"]
    for kw, s in ranked[:15]:
        rate = s["approved"] / max(s["collected"], 1) * 100
        line = f"[{s['category']}] {kw}: {s['approved']}/{s['collected']} ({rate:.1f}%)"
        lines.append(line)
        log.info(line)

    zero_approval = [kw for kw, s in stats.items() if is_replacement_candidate(s)]
    if zero_approval:
        lines.append(f"\n승인 0건(수집 20건 이상) 검색어 — 교체 검토 대상: {', '.join(zero_approval)}")
        log.info("승인 0건 검색어: %s", zero_approval)

    telegram_client.send_message("\n".join(lines))


if __name__ == "__main__":
    main()

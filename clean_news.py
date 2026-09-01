"""수집된 뉴스를 정제한다: 최신성/길이 필터 + 제목 유사도 기반 중복 제거.

- MAX_AGE_HOURS 이내(published_at 기준, 없으면 collected_at) 기사만 후보로 남긴다.
- summary 길이가 MIN_LENGTH 미만인 저품질 기사는 제외한다.
- rapidfuzz로 같은 카테고리 내 제목 유사도가 DEDUP_SIMILARITY_THRESHOLD 이상인
  기사끼리 dedup_group_id로 묶고, 그룹당 대표 기사 1건만 status='filtered'로 승격한다.
  (대표 기사는 그룹 내에서 published_at이 가장 이른 기사)

실행:
    python clean_news.py
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz

import config
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("clean_news")


def fetch_collected() -> list[dict]:
    client = get_client()
    resp = (
        client.table("news_items")
        .select("id,title,summary,category,published_at,collected_at")
        .eq("status", "collected")
        .execute()
    )
    return resp.data or []


def age_ok(item: dict) -> bool:
    ts = item.get("published_at") or item.get("collected_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=config.MAX_AGE_HOURS)


def length_ok(item: dict) -> bool:
    return len(item.get("summary") or "") >= config.MIN_LENGTH


def cluster_by_category(items: list[dict]) -> list[list[dict]]:
    """카테고리별로 제목 유사도 기반 그리디 클러스터링."""
    clusters: list[list[dict]] = []
    for item in items:
        placed = False
        for cluster in clusters:
            rep = cluster[0]
            if rep["category"] != item["category"]:
                continue
            score = fuzz.token_sort_ratio(rep["title"], item["title"])
            if score >= config.DEDUP_SIMILARITY_THRESHOLD:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def pick_representative(cluster: list[dict]) -> dict:
    def sort_key(it):
        ts = it.get("published_at") or it.get("collected_at") or ""
        return ts

    return sorted(cluster, key=sort_key)[0]


def apply_updates(clusters: list[list[dict]]) -> tuple[int, int]:
    client = get_client()
    grouped = 0
    promoted = 0
    for cluster in clusters:
        rep = pick_representative(cluster)
        group_id = rep["id"]
        ids = [it["id"] for it in cluster]

        client.table("news_items").update({"dedup_group_id": group_id}).in_(
            "id", ids
        ).execute()
        grouped += len(ids)

        client.table("news_items").update({"status": "filtered"}).eq("id", rep["id"]).execute()
        promoted += 1
    return grouped, promoted


def main():
    items = fetch_collected()
    log.info("정제 대상 (status=collected): %d건", len(items))

    candidates = [it for it in items if age_ok(it) and length_ok(it)]
    log.info("최신성/길이 필터 통과: %d건", len(candidates))

    by_category = defaultdict(list)
    for it in candidates:
        by_category[it["category"]].append(it)

    all_clusters = []
    for category, cat_items in by_category.items():
        clusters = cluster_by_category(cat_items)
        log.info("[%s] %d건 -> %d개 그룹", category, len(cat_items), len(clusters))
        all_clusters.extend(clusters)

    grouped, promoted = apply_updates(all_clusters)
    log.info("dedup_group_id 부여 %d건, status=filtered 승격 %d건", grouped, promoted)


if __name__ == "__main__":
    main()

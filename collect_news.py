"""카테고리별 검색어로 Google News RSS를 수집해 Supabase news_items에 저장한다.

실행:
    python collect_news.py
"""

import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw: str) -> str:
    return _TAG_RE.sub("", raw or "").strip()

import config
import events
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("collect_news")

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
REQUEST_DELAY_SEC = 1.0


def load_keywords(path: str = "keywords.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {cat: kws for cat, kws in data.items() if cat in config.CATEGORIES}


def fetch_entries(keyword: str) -> list:
    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(keyword))
    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        log.warning("피드 파싱 실패: %s (%s)", keyword, feed.bozo_exception)
    return feed.entries


def entry_to_row(entry, category: str, keyword: str) -> dict | None:
    title = getattr(entry, "title", None)
    link = getattr(entry, "link", None)
    if not title or not link:
        return None

    source = None
    if hasattr(entry, "source") and getattr(entry.source, "title", None):
        source = entry.source.title

    published_at = None
    if getattr(entry, "published_parsed", None):
        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()

    summary = strip_html(getattr(entry, "summary", ""))

    return {
        "title": title,
        "url": link,
        "source": source,
        "category": category,
        "keyword": keyword,
        "summary": summary,
        "published_at": published_at,
        "status": "collected",
    }


def is_recent(row: dict, cutoff: datetime) -> bool:
    # 발행 시각이 없는 기사는 정제 단계가 수집 시각으로 판단하므로 남겨둔다.
    return not row["published_at"] or datetime.fromisoformat(row["published_at"]) >= cutoff


def collect_all() -> tuple[list[dict], int]:
    """검색어마다 RSS 기사(보통 100건 안팎) 중 최근 MAX_AGE_HOURS 이내 기사만, 최신순
    COLLECT_MAX_PER_KEYWORD건까지 남긴다. 2026-10-29 측정: 하루 4,489건을 받아 2,597건을
    저장했지만 28%는 정제에서 바로 버려지는 옛 기사였고, 자동 선정이 보는 후보는
    카테고리당 20건뿐이라 나머지는 쓰이지 않고 쌓이기만 했다."""
    keywords_by_category = load_keywords()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.MAX_AGE_HOURS)
    rows = []
    fetched_total = 0
    for category, keywords in keywords_by_category.items():
        for keyword in keywords:
            try:
                entries = fetch_entries(keyword)
            except Exception as e:
                log.error("수집 실패 [%s/%s]: %s", category, keyword, e)
                continue

            candidates = [row for entry in entries if (row := entry_to_row(entry, category, keyword))]
            fetched_total += len(candidates)
            recent = [row for row in candidates if is_recent(row, cutoff)]
            recent.sort(key=lambda r: r["published_at"] or "", reverse=True)
            kept = recent[: config.COLLECT_MAX_PER_KEYWORD]
            rows.extend(kept)
            log.info("[%s] '%s' -> %d건 중 최근 %d건, 저장 대상 %d건", category, keyword, len(candidates), len(recent), len(kept))
            time.sleep(REQUEST_DELAY_SEC)
    return rows, fetched_total


def save_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    client = get_client()
    saved = 0
    # url UNIQUE 제약이 있으므로 중복은 무시하고, 배치로 upsert
    batch_size = 200
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            resp = (
                client.table("news_items")
                .upsert(batch, on_conflict="url", ignore_duplicates=True)
                .execute()
            )
            saved += len(resp.data or [])
        except Exception as e:
            log.error("Supabase 저장 실패 (batch %d): %s", i, e)
    return saved


def main():
    log.info("뉴스 수집 시작")
    events.record("collect", "started", message="Google News RSS 수집 시작")
    rows, fetched = collect_all()
    log.info("RSS %d건 중 저장 대상 %d건(최근·검색어당 상한 적용), Supabase 저장 시도", fetched, len(rows))
    saved = save_rows(rows)
    log.info("신규 저장 %d건 완료", saved)
    events.record(
        "collect", "success" if rows else "failed", count=len(rows),
        message=f"수집 {len(rows):,}건 (RSS {fetched:,}건 중, 신규 저장 {saved:,}건)",
        detail={"fetched": fetched, "kept": len(rows), "saved": saved},
    )


if __name__ == "__main__":
    main()

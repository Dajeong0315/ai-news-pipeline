"""카테고리별 검색어로 Google News RSS를 수집해 Supabase news_items에 저장한다.

실행:
    python collect_news.py
"""

import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone

import feedparser

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw: str) -> str:
    return _TAG_RE.sub("", raw or "").strip()

import config
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


def collect_all() -> list[dict]:
    keywords_by_category = load_keywords()
    rows = []
    for category, keywords in keywords_by_category.items():
        for keyword in keywords:
            try:
                entries = fetch_entries(keyword)
            except Exception as e:
                log.error("수집 실패 [%s/%s]: %s", category, keyword, e)
                continue

            count = 0
            for entry in entries:
                row = entry_to_row(entry, category, keyword)
                if row:
                    rows.append(row)
                    count += 1
            log.info("[%s] '%s' -> %d건", category, keyword, count)
            time.sleep(REQUEST_DELAY_SEC)
    return rows


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
    rows = collect_all()
    log.info("총 수집 %d건 (중복 URL 포함), Supabase 저장 시도", len(rows))
    saved = save_rows(rows)
    log.info("신규 저장 %d건 완료", saved)


if __name__ == "__main__":
    main()

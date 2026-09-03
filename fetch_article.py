"""뉴스 원문 URL에서 본문 텍스트를 추출한다.

RSS의 summary는 실제 본문이 아니라 "제목+언론사" 재구성 문자열이라(clean_news.py
설계결정 참고), 카드 3~5장짜리 실제 내용을 만들려면 원문을 직접 읽어야 한다.
trafilatura로 추출하며, 실패(페이월/차단/파싱 실패)하면 빈 문자열을 반환하고
호출 측이 제목만으로 대체 처리하도록 한다.
"""

import logging
import subprocess

import trafilatura

log = logging.getLogger("fetch_article")

MAX_CHARS = 4000  # LLM 프롬프트에 넣을 본문 길이 상한(토큰/비용 절약)
RESOLVE_SCRIPT = "assets/templates/resolve_url.js"


def resolve_google_news_url(url: str) -> str:
    """Google News RSS 링크(news.google.com/rss/articles/...)는 JS 리다이렉트라
    실제 언론사 URL을 얻으려면 헤드리스 브라우저로 열어봐야 한다."""
    if "news.google.com" not in url:
        return url
    try:
        result = subprocess.run(
            ["node", RESOLVE_SCRIPT, url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        resolved = result.stdout.strip()
        return resolved if resolved else url
    except Exception as e:
        log.warning("URL 리졸브 실패 [%s]: %s", url, e)
        return url


def fetch_article_text(url: str) -> str:
    real_url = resolve_google_news_url(url)
    try:
        downloaded = trafilatura.fetch_url(real_url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False) or ""
        return text.strip()[:MAX_CHARS]
    except Exception as e:
        log.warning("본문 추출 실패 [%s]: %s", real_url, e)
        return ""

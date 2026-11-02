"""뉴스 원문 URL에서 본문 텍스트를 추출한다.

RSS의 summary는 실제 본문이 아니라 "제목+언론사" 재구성 문자열이라(clean_news.py
설계결정 참고), 카드 3~5장짜리 실제 내용을 만들려면 원문을 직접 읽어야 한다.
trafilatura로 추출하며, 실패(페이월/차단/파싱 실패)하면 빈 문자열을 반환하고
호출 측이 제목만으로 대체 처리하도록 한다.
"""

import logging
import subprocess

import httpx
import trafilatura

log = logging.getLogger("fetch_article")

MAX_CHARS = 4000  # LLM 프롬프트에 넣을 본문 길이 상한(토큰/비용 절약)
RESOLVE_SCRIPT = "assets/templates/resolve_url.js"
# 본문은 IPv4 고정 httpx로 받는다(2026-11-02). trafilatura.fetch_url은 이 PC에서 매일경제 등에 SSL EOF로
# 몇 분 걸리다 실패했는데(11/2 dry-run 1등 기사 0자), IPv4 고정으로는 0.1초에 받아졌다(배경 생성과 같은 이유).
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0 Safari/537.36", "Accept-Language": "ko-KR,ko;q=0.9"}


def download(url: str) -> str:
    try:
        with httpx.Client(transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1), headers=_HEADERS,
                          timeout=20, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        log.warning("본문 받기 실패(IPv4), trafilatura로 재시도 [%s]: %s", url, e)
        return trafilatura.fetch_url(url) or ""


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
    if "news.google.com" in real_url:  # 헤드리스 리다이렉트가 가끔 실패한다(11/2 dry-run 1등 기사 0자) → 한 번 더
        real_url = resolve_google_news_url(url)
    if "news.google.com" in real_url:  # 구글 주소로 직접 받으면 SSL 오류로 몇 분 걸리고 본문도 없다
        log.warning("구글 뉴스 주소를 언론사 주소로 못 풀어 본문을 건너뜀: %s", url)
        return ""
    if "/error" in real_url:  # 언론사가 자동 접속을 막고 오류 페이지로 보냄(11/2 KBS)
        log.warning("언론사 오류 페이지로 이동해 본문을 건너뜀: %s", real_url)
        return ""
    try:
        downloaded = download(real_url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False) or ""
        return text.strip()[:MAX_CHARS]
    except Exception as e:
        log.warning("본문 추출 실패 [%s]: %s", real_url, e)
        return ""

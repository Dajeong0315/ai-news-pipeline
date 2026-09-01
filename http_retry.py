"""Cloudflare Workers AI 등 외부 API 호출용 재시도(지수 백오프) 래퍼.

일시적 오류(5xx, 타임아웃/연결 오류)에서만 재시도하고, 4xx 등 클라이언트
오류는 즉시 예외를 올린다(재시도해도 성공할 가능성이 없으므로).
"""

import logging
import time

import httpx

log = logging.getLogger("http_retry")


def post_with_retry(url: str, max_attempts: int = 3, backoff_base: float = 2.0, **kwargs) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = httpx.post(url, **kwargs)
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            last_exc = e
            if attempt == max_attempts:
                raise
            wait = backoff_base**attempt
            log.warning("요청 실패(%s), %.1f초 후 재시도 (%d/%d): %s", url, wait, attempt, max_attempts, e)
            time.sleep(wait)
    raise last_exc

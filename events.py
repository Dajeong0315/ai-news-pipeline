"""파이프라인 단계별 이벤트를 Supabase pipeline_events에 기록한다 (대시보드 [오늘] 타임라인용).

한 프로세스 실행(run_daily.py 1회)이 run_id 하나다. GitHub Actions에서는
PIPELINE_RUN_ID로 워크플로 실행 ID를 넘겨 같은 run으로 묶는다.

기록 실패(테이블 없음, 네트워크 오류 등)가 카드 생성·게시를 멈추면 안 되므로
record()는 예외를 밖으로 던지지 않고 경고 로그만 남긴다.
"""

import logging
import os
import uuid
import socket
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

import config
from db import get_client

log = logging.getLogger("events")

KST = timezone(timedelta(hours=9))
RUN_ID = os.environ.get("PIPELINE_RUN_ID") or (
    datetime.now(KST).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
)
TRIGGER = os.environ.get("PIPELINE_TRIGGER", "local")


try:
    if not (Path(__file__).parent / '.git').exists():
        raise RuntimeError('Archive source; do not inherit an enclosing repository SHA')
    COMMIT = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, text=True).strip()
except Exception:
    COMMIT = "archive-source:6e951ec0812ef8ea9e901ad385d57e1ad9e7bdec+operating-patch+recovery"
IDENTITY = {"host": socket.gethostname(), "pid": os.getpid(), "commit": COMMIT}

def record(
    stage: str,
    status: str,
    *,
    category: str | None = None,
    count: int | None = None,
    message: str | None = None,
    detail: dict | list | None = None,
    news_item_id: int | None = None,
    card_id: int | None = None,
) -> None:
    log.info("run_id=%s channel=%s stage=%s status=%s", RUN_ID, config.CHANNEL, stage, status)
    detail = {**IDENTITY, **(detail if isinstance(detail, dict) else {"payload": detail})}
    detail = __import__("json").loads(config.redact(__import__("json").dumps(detail, ensure_ascii=False)))
    row = {
        "run_id": RUN_ID,
        "channel": config.CHANNEL,
        "stage": stage,
        "status": status,
        "category": category,
        "count": count,
        "message": config.redact(message)[:1000] if message else None,
        "detail": detail,
        "news_item_id": news_item_id,
        "card_id": card_id,
    }
    try:
        get_client().table("pipeline_events").insert(row).execute()
    except Exception as e:
        log.warning("pipeline_events 기록 실패(%s/%s): %s", stage, status, e)

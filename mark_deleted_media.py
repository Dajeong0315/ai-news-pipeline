"""게시 이력(pipeline_events·로그)에 남은 인스타 media_id 중 인스타에서 지워진 것을 표시만 한다.

2026-10-31 운영자가 경제 채널 9~10월 저품질 게시물을 인스타에서 직접 지웠다. 기록은 지우지 않는다:
cards.published·news_items는 그대로 둬야 대시보드 게시 수와 중복 선정 검사(같은 기사 다시 올리지 않기)가 유지된다.
- Graph API로 media_id를 하나씩 조회해 "객체 없음"(code 100) 오류면 삭제로 본다. 토큰 오류 등 다른 오류는 판단 보류.
- 표시: channels/<채널>/deleted_media.json(로그에만 있는 id 포함 전체)과, pipeline_events에 있는 게시 성공 행이면
  detail에 ig_deleted·ig_deleted_checked_at을 덧붙인다(status는 그대로라 대시보드 집계는 바뀌지 않음).

- 2026-11-02: cards.media_id가 지워졌으면 cards.publish_status='deleted'(대시보드 카드 탭 "삭제됨" 표시).
  run_daily가 실행마다 daily()를 불러 채널마다 하루 1번만 조회한다(장부 "_checked_on").

실행:
    python mark_deleted_media.py            # 조회 + 표시
    python mark_deleted_media.py --dry-run  # 조회만
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from postgrest.exceptions import APIError

import config
import instagram_client
from db import get_client

KST = timezone(timedelta(hours=9))
LEDGER = config.CHANNEL_DIR / "deleted_media.json"
# 경제 채널 로그(run_collect.bat=auto, run_publish.bat=publish). 오래된 줄은 cp949로 남아 바이트로 읽는다.
LOG_FILES = {"economy": ("logs/publish.log", "logs/collect.log"), "youth": ("logs/youth.log",)}
_LOG_RE = re.compile(rb"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[([a-z_]+)\].*?media_id=(\d+)", re.MULTILINE)


def ids_from_events() -> dict[str, dict]:
    rows = (get_client().table("pipeline_events").select("id,stage,category,card_id,detail,created_at")
            .eq("channel", config.CHANNEL).in_("stage", ["publish", "reel"]).eq("status", "success").execute().data or [])
    return {str(r["detail"]["media_id"]): {"event_id": r["id"], "stage": r["stage"], "category": r["category"],
                                            "card_id": r["card_id"], "posted_at": r["created_at"]}
            for r in rows if isinstance(r.get("detail"), dict) and r["detail"].get("media_id")}


def ids_from_logs() -> dict[str, dict]:
    found = {}
    for name in LOG_FILES.get(config.CHANNEL, ()):
        path = Path(name)
        if path.exists():
            for ts, category, media_id in _LOG_RE.findall(path.read_bytes()):
                found.setdefault(media_id.decode(), {"category": category.decode(), "posted_at": ts.decode(), "log": name})
    return found


# 연결 하나를 IPv4로 고정해 같이 쓴다. 이 PC는 새 연결마다 ~25초 걸려 게시물 수십 개 조회가 몇 분씩 걸렸다(2026-11-02).
_HTTP = httpx.Client(transport=httpx.HTTPTransport(local_address="0.0.0.0"), timeout=15)


def check(media_id: str) -> tuple[str, str]:
    """('exists'|'deleted'|'unknown', 설명)"""
    resp = _HTTP.get(f"{instagram_client.GRAPH_BASE}/{media_id}", params={"fields": "id,timestamp,permalink"},
                     headers=instagram_client._auth_header())
    if resp.status_code < 400:
        return "exists", resp.json().get("permalink", "")
    try:
        error = resp.json().get("error", {})
    except ValueError:
        error = {}
    message = f"{resp.status_code} code={error.get('code')} subcode={error.get('error_subcode')}: {error.get('message', resp.text)[:160]}"
    # 지워진 게시물: code 100(subcode 33 "Object with ID ... does not exist"). 권한·토큰 오류(190·10·200 등)는 보류
    return ("deleted" if error.get("code") == 100 else "unknown"), message


def ids_from_cards() -> dict[str, dict]:
    rows = (get_client().table("cards").select("id,category,media_id,published_at,publish_status")
            .in_("category", list(config.CATEGORIES)).eq("published", True).not_.is_("media_id", "null").execute().data or [])
    return {str(r["media_id"]): {"card_id": r["id"], "category": r["category"], "posted_at": r.get("published_at") or "",
                                 "card_status": r.get("publish_status")} for r in rows}


def card_by_date(info: dict) -> int | None:
    """media_id가 cards에 없는 옛 게시물(10월 이전)은 같은 분야·같은 날 게시된 카드가 하나뿐이면 그 카드로 본다."""
    day, category = str(info.get("posted_at") or "")[:10], info.get("category")
    if not day or not category:
        return None
    start = datetime.fromisoformat(day).replace(tzinfo=KST)
    rows = (get_client().table("cards").select("id").eq("category", category).eq("published", True)
            .gte("created_at", start.astimezone(timezone.utc).isoformat())
            .lt("created_at", (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()).execute().data or [])
    return rows[0]["id"] if len(rows) == 1 else None


def daily() -> None:
    """채널마다 하루 1번만 조회한다(run_daily가 실행마다 부름). 실패해도 파이프라인을 멈추지 않게 호출 측이 잡는다."""
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    today = datetime.now(KST).date().isoformat()
    if ledger.get("_checked_on") == today:
        return
    main([])


def main(argv: list[str] | None = None) -> None:
    dry_run = "--dry-run" in (sys.argv[1:] if argv is None else argv)
    instagram_client.verify_token()  # 토큰이 죽어 있으면 전부 "없음"처럼 보일 수 있으니 먼저 끊는다
    media = ids_from_logs()
    for media_id, info in ids_from_events().items():
        media[media_id] = {**media.get(media_id, {}), **info}
    for media_id, info in ids_from_cards().items():
        media[media_id] = {**media.get(media_id, {}), **info}
    now = datetime.now(KST).isoformat(timespec="seconds")
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    counts = {"exists": 0, "deleted": 0, "unknown": 0}
    status_blocked = False
    for media_id, info in sorted(media.items(), key=lambda kv: kv[1].get("posted_at", "")):
        state, note = check(media_id)
        counts[state] += 1
        print(f"{state:8} {media_id} {info.get('posted_at', '')[:16]} [{info.get('category')}] {note}")
        if state != "deleted":
            continue
        ledger[media_id] = {**info, "ig_deleted": True, "checked_at": now, "error": note}
        card_id = info.get("card_id") or card_by_date(info)
        if not dry_run and card_id and info.get("card_status") != "deleted" and not status_blocked:
            try:
                get_client().table("cards").update({"publish_status": "deleted"}).eq("id", card_id).execute()
            except APIError as e:  # sql/2026-11-02_publish_status_deleted.sql 적용 전(허용 값에 deleted 없음)
                if e.code != "23514":
                    raise
                status_blocked = True
                print("cards.publish_status에 'deleted'를 아직 못 씀 — sql/2026-11-02_publish_status_deleted.sql 적용 필요(카드 표시만 건너뜀)")
        if not dry_run and info.get("event_id"):
            row = get_client().table("pipeline_events").select("detail").eq("id", info["event_id"]).execute().data[0]
            detail = {**(row.get("detail") or {}), "ig_deleted": True, "ig_deleted_checked_at": now}
            get_client().table("pipeline_events").update({"detail": detail}).eq("id", info["event_id"]).execute()
    if not dry_run:
        ledger["_checked_on"] = now[:10]
        LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"조회 {len(media)}건: 있음 {counts['exists']} · 삭제 {counts['deleted']} · 판단 보류 {counts['unknown']}"
          f"{' (dry-run, 기록 안 함)' if dry_run else f' → {LEDGER}'}")


if __name__ == "__main__":
    main()

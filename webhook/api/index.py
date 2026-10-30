"""텔레그램 승인 웹훅 (Vercel Serverless Function, FastAPI).

Vercel 배포 시 이 파일 하나만 번들되므로, 루트 프로젝트의 config.py/db.py를
재사용하지 않고 Supabase REST API를 httpx로 직접 호출하는 독립 모듈로 구성한다.

필요 환경변수 (Vercel 프로젝트 설정 > Environment Variables):
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
    DASHBOARD_PASSWORD, DASHBOARD_SESSION_SECRET  (운영 대시보드 로그인)
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
MAX_BACKLOG_DAYS = int(os.environ.get("MAX_BACKLOG_DAYS", 3))

KST = timezone(timedelta(hours=9))
# 채널(인스타그램 계정)별 대시보드 설정(2026-10-30). Vercel에는 이 파일만 배포되므로
# channels/<채널>/channel.json의 이름·카테고리를 여기에 옮겨 둔다. 카테고리를 바꾸면 여기도 같이 고칠 것.
# daily_posts: 하루 게시 수를 정한 채널(세 분야 후보 중 1~N건 선정). None이면 카테고리별 1건.
CHANNELS = {
    "economy": {
        "name": "묘한 경제", "handle": "@myohan.economy", "daily_posts": None,
        "categories": {"index_macro": "지수/거시", "stock": "개별종목", "policy_industry": "정책/산업"},
    },
    "youth": {
        "name": "정보담당", "handle": "", "daily_posts": 2,
        "categories": {"youth_support": "지원금/제도", "youth_job": "채용/취업·자격증", "youth_activity": "대외활동/공모전"},
    },
}
DEFAULT_CHANNEL = "economy"
CATEGORY_LABELS = {k: v for ch in CHANNELS.values() for k, v in ch["categories"].items()}


def sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sb_get(path: str, params: dict) -> list:
    resp = httpx.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=sb_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def sb_patch(path: str, params: dict, body: dict) -> list:
    resp = httpx.patch(
        f"{SUPABASE_URL}/rest/v1/{path}", headers=sb_headers(), params=params, json=body, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def tg_call(method: str, payload: dict) -> dict:
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}", json=payload, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def today_start_utc_iso() -> str:
    now_kst = datetime.now(KST)
    start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_kst.astimezone(timezone.utc).isoformat()


def get_allowed_quota(category: str, as_of: date | None = None) -> int:
    """이월 큐 로직: 최근 카드 발행이 없었던 연속 일수만큼 오늘 승인 가능
    개수를 늘려준다. generate_cardset.py가 만드는 cards 테이블 기준(REST 버전).
    as_of를 주면 그 날짜 기준으로 계산한다(대시보드의 "내일 이월 쿼터")."""
    rows = sb_get(
        "cards",
        {
            "select": "created_at",
            "category": f"eq.{category}",
            "order": "created_at.desc",
            "limit": str(MAX_BACKLOG_DAYS + 5),
        },
    )
    have_dates = {
        datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(KST).date()
        for r in rows
    }
    if not have_dates:
        return 1

    earliest_date = min(have_dates)
    missed = 0
    day = (as_of or datetime.now(KST).date()) - timedelta(days=1)
    while missed < MAX_BACKLOG_DAYS and day >= earliest_date and day not in have_dates:
        missed += 1
        day -= timedelta(days=1)
    return missed + 1


def category_quota_reached(category: str) -> bool:
    rows = sb_get(
        "news_items",
        {
            "select": "id",
            "category": f"eq.{category}",
            "status": "eq.approved",
            "collected_at": f"gte.{today_start_utc_iso()}",
        },
    )
    return len(rows) >= get_allowed_quota(category)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 운영 대시보드 (2026-10-29, 기존 /status 대체)
#
# - 비밀번호 1개 로그인. 세션은 HMAC 서명 쿠키라 서버 상태가 필요 없다.
#   DASHBOARD_PASSWORD / DASHBOARD_SESSION_SECRET이 비어 있으면 로그인이
#   항상 실패한다(fail closed). 로그인 전에는 /api/dash/*가 401만 돌려준다.
# - service_role 키는 서버에서만 쓰고, 브라우저는 /api/dash/* JSON만 받는다.
# - 실시간 반영은 Supabase Realtime 대신 이 API를 4초마다 폴링한다
#   (브라우저용 JWT·RLS 정책 없이 키를 서버 밖으로 내보내지 않기 위함, 운영자 확인 완료).
# ---------------------------------------------------------------------------

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DASHBOARD_SESSION_SECRET = os.environ.get("DASHBOARD_SESSION_SECRET", "")
SESSION_COOKIE = "mh_session"
SESSION_DAYS = 30
STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "cards")
PAGE_SIZE = 1000


def channel_ctx(channel: str | None) -> dict:
    """대시보드 API의 ?channel= 값 → 그 채널의 카테고리·필터(2026-10-30 채널 전환). 모르는 값은 400."""
    key = channel or DEFAULT_CHANNEL
    if key not in CHANNELS:
        raise HTTPException(status_code=400, detail=f"unknown channel: {key}")
    cfg = CHANNELS[key]
    cats = tuple(cfg["categories"])
    return {
        "key": key,
        "cfg": cfg,
        "categories": cats,
        "channel_filter": f"eq.{key}",
        "category_filter": f"in.({','.join(cats)})",
        "target": cfg["daily_posts"] or len(cats),
    }


def channels_for_page() -> str:
    """화면 스크립트에 넣을 채널 목록 JSON(</script> 조기 종료 방지)."""
    data = {k: {"name": v["name"], "handle": v["handle"], "daily_posts": v["daily_posts"], "categories": v["categories"]}
            for k, v in CHANNELS.items()}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _sign(payload: str) -> str:
    return hmac.new(DASHBOARD_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_token() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_sign(f'v1.{exp}')}"


def is_authenticated(request: Request) -> bool:
    if not DASHBOARD_PASSWORD or not DASHBOARD_SESSION_SECRET:
        return False
    token = request.cookies.get(SESSION_COOKIE, "")
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig, _sign(f"v1.{exp}"))


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="login required")


def sb_get_all(path: str, params: dict) -> list:
    """PostgREST는 한 번에 최대 1000행만 주므로 끝까지 페이지네이션한다."""
    rows: list = []
    offset = 0
    while True:
        batch = sb_get(path, {**params, "limit": str(PAGE_SIZE), "offset": str(offset)})
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def sb_count(path: str, params: dict) -> int:
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={**sb_headers(), "Prefer": "count=exact"},
        params={**params, "select": "id", "limit": "1"},
        timeout=15,
    )
    resp.raise_for_status()
    return int(resp.headers.get("content-range", "*/0").rsplit("/", 1)[-1] or 0)


def kst_day_range(day: date) -> tuple[str, str]:
    start = datetime.combine(day, datetime.min.time(), KST)
    return start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def to_kst(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)


def parse_day(value: str | None) -> date:
    try:
        return date.fromisoformat(value) if value else datetime.now(KST).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


def public_url(storage_path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{storage_path}"


def card_prefix(image_path: str) -> str:
    """cards.image_path(로컬 경로 output\\2026-10-27\\stock)를 스토리지 경로(2026-10-27/stock)로."""
    parts = [p for p in image_path.replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:])


def list_slides(image_path: str) -> list[str]:
    prefix = card_prefix(image_path)
    resp = httpx.post(
        f"{SUPABASE_URL}/storage/v1/object/list/{STORAGE_BUCKET}",
        headers=sb_headers(),
        json={"prefix": prefix, "limit": 100, "sortBy": {"column": "name", "order": "asc"}},
        timeout=15,
    )
    resp.raise_for_status()
    names = sorted(o["name"] for o in resp.json() if o.get("name", "").endswith(".png"))
    return [public_url(f"{prefix}/{n}") for n in names]


def event_view(e: dict) -> dict:
    return {
        "time": to_kst(e["created_at"]).strftime("%H:%M:%S"),
        "date": to_kst(e["created_at"]).strftime("%m/%d"),
        "run_id": e["run_id"],
        "stage": e["stage"],
        "status": e["status"],
        "category": e.get("category"),
        "count": e.get("count"),
        "message": e.get("message"),
    }


def category_state(category: str, cards: list, selected: list, events: list) -> dict:
    """오늘 카테고리 하나가 공장 어디쯤 있는지. 뒤 단계일수록 우선한다."""
    cat_events = [e for e in events if e.get("category") == category]
    last = {}
    for e in cat_events:  # created_at 오름차순이므로 마지막 값이 최신
        last[e["stage"]] = e

    card = next((c for c in reversed(cards) if c["category"] == category), None)
    news = next((n for n in reversed(selected) if n["category"] == category), None)
    publish_ev = last.get("publish")
    cardset_ev = last.get("cardset")

    if card and card["published"]:
        state = "published"
    elif card and publish_ev and publish_ev["status"] == "failed":
        state = "failed"
    elif card and publish_ev and publish_ev["status"] == "started":
        state = "publishing"
    elif card:
        state = "carded"
    elif cardset_ev and cardset_ev["status"] == "failed":
        state = "failed"
    elif cardset_ev and cardset_ev["status"] in ("started", "retry"):
        state = "making"
    elif news:
        state = "selected"
    elif last.get("select", {}).get("status") == "skipped":
        state = "empty"
    else:
        state = "waiting"

    reason = None
    if state == "failed":
        reason = (publish_ev if card else cardset_ev).get("message")
    retries = sum(1 for e in cat_events if e["stage"] == "cardset" and e["status"] == "retry")
    return {
        "key": category,
        "label": CATEGORY_LABELS[category],
        "state": state,
        "reason": reason,
        "retries": retries,
        "news": news and {k: news.get(k) for k in ("id", "title", "source", "url")},
        "card": card and {
            "id": card["id"],
            "title": card["final_title"],
            "published": card["published"],
            "cover": public_url(f"{card_prefix(card['image_path'])}/01.png"),
        },
    }


def render_login(error: str, status_code: int = 200) -> HTMLResponse:
    html = LOGIN_HTML.replace("{storage_base}", public_url("")).replace("{error}", error)
    return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    if not is_authenticated(request):
        return render_login("")
    return HTMLResponse(
        DASHBOARD_HTML.replace("{storage_base}", public_url("")).replace("{channels_json}", channels_for_page()),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/status")
async def status_redirect():
    return RedirectResponse("/", status_code=307)


@app.post("/login")
async def login(request: Request):
    form = urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"))
    password = (form.get("password") or [""])[0]
    if not DASHBOARD_PASSWORD or not DASHBOARD_SESSION_SECRET or not hmac.compare_digest(
        password.encode(), DASHBOARD_PASSWORD.encode()
    ):
        await asyncio.sleep(1.0)  # 무차별 대입 속도를 늦춘다
        return render_login('<p class="err">비밀번호가 맞지 않아요</p>', status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, make_session_token(), max_age=SESSION_DAYS * 86400,
        httponly=True, secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
        samesite="lax", path="/",
    )
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/api/dash/today", dependencies=[Depends(require_auth)])
async def dash_today(channel: str | None = None):
    ch = channel_ctx(channel)
    today = datetime.now(KST).date()
    start, end = kst_day_range(today)
    events = sb_get_all(
        "pipeline_events",
        {"select": "*", "channel": ch["channel_filter"], "created_at": f"gte.{start}", "order": "created_at.asc,id.asc"},
    )
    showing = "today"
    if not events:
        # 오늘 아직 실행 전이면 가장 최근 실행을 보여준다(빈 화면 대신).
        latest = sb_get("pipeline_events", {"select": "run_id", "channel": ch["channel_filter"], "order": "created_at.desc", "limit": "1"})
        if latest:
            events = sb_get_all(
                "pipeline_events",
                {"select": "*", "run_id": f"eq.{latest[0]['run_id']}", "order": "created_at.asc,id.asc"},
            )
            showing = "latest_run"

    if showing == "latest_run" and events:
        start, end = kst_day_range(to_kst(events[0]["created_at"]).date())
    cards = sb_get_all(
        "cards",
        {"select": "id,category,final_title,image_path,published,created_at", "category": ch["category_filter"],
         "and": f"(created_at.gte.{start},created_at.lt.{end})", "order": "created_at.asc"},
    )
    selected = sb_get_all(
        "news_items",
        {"select": "id,category,title,source,url,collected_at", "status": "eq.approved", "category": ch["category_filter"],
         "and": f"(collected_at.gte.{start},collected_at.lt.{end})", "order": "collected_at.asc"},
    )

    def stage_count(stage: str) -> int | None:
        hits = [e for e in events if e["stage"] == stage and e["status"] == "success"]
        return hits[-1]["count"] if hits else None

    runs = [e for e in events if e["stage"] == "run"]
    return {
        "channel": ch["key"],
        "now": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "showing": showing,
        "day": to_kst(events[0]["created_at"]).strftime("%Y-%m-%d") if events else today.isoformat(),
        "kpi": {
            "collected": stage_count("collect"),
            "cleaned": stage_count("clean"),
            "selected": len(selected) if ch["cfg"]["daily_posts"] else len({n["category"] for n in selected}),
            "published": sum(1 for c in cards if c["published"]),
            "target": ch["target"],
        },
        "run": {
            "status": runs[-1]["status"],
            "time": to_kst(runs[-1]["created_at"]).strftime("%H:%M"),
            "trigger": (runs[0].get("detail") or {}).get("trigger"),
        } if runs else None,
        "categories": [category_state(c, cards, selected, events) for c in ch["categories"]],
        "timeline": [event_view(e) for e in events],
    }


@app.get("/api/dash/topics", dependencies=[Depends(require_auth)])
async def dash_topics(day_param: str | None = Query(None, alias="date"), pool: int = 20, channel: str | None = None):
    ch = channel_ctx(channel)
    day = parse_day(day_param)
    start, end = kst_day_range(day)
    window = f"(collected_at.gte.{start},collected_at.lt.{end})"
    selected = sb_get_all(
        "news_items",
        {"select": "id,category,title,source,url,keyword,published_at", "status": "eq.approved",
         "category": ch["category_filter"], "and": window},
    )
    result = []
    for category in ch["categories"]:
        candidates = sb_get(
            "news_items",
            {"select": "id,title,source,url,keyword,published_at,status", "category": f"eq.{category}",
             "status": "in.(filtered,pending_approval,approved)", "and": window,
             "order": "published_at.desc.nullslast", "limit": str(max(1, min(pool, 100)))},
        )
        total = sb_count("news_items", {"category": f"eq.{category}",
                                        "status": "in.(filtered,pending_approval,approved)", "and": window})
        result.append({
            "key": category,
            "label": CATEGORY_LABELS[category],
            "selected": [s for s in selected if s["category"] == category],
            "candidates": candidates,
            "total": total,
        })
    return {"date": day.isoformat(), "categories": result}


@app.get("/api/dash/keywords", dependencies=[Depends(require_auth)])
async def dash_keywords(channel: str | None = None):
    ch = channel_ctx(channel)
    latest = sb_get(
        "pipeline_events",
        {"select": "created_at,detail", "stage": "eq.keyword_stats", "status": "eq.success", "channel": ch["channel_filter"],
         "order": "created_at.desc", "limit": "1"},
    )
    if not latest:
        return {"updated": None, "rows": []}
    detail = latest[0]["detail"] or {}
    return {
        "updated": to_kst(latest[0]["created_at"]).strftime("%Y-%m-%d %H:%M"),
        "days": detail.get("days"),
        "min_collected": detail.get("min_collected"),
        "rows": detail.get("rows", []),
    }


@app.get("/api/dash/calendar", dependencies=[Depends(require_auth)])
async def dash_calendar(days: int = 30, channel: str | None = None):
    ch = channel_ctx(channel)
    days = max(7, min(days, 90))
    today = datetime.now(KST).date()
    first = today - timedelta(days=days - 1)
    start, _ = kst_day_range(first)
    cards = sb_get_all(
        "cards",
        {"select": "category,published,created_at,final_title", "category": ch["category_filter"],
         "created_at": f"gte.{start}", "order": "id.asc"},
    )
    failures = sb_get_all(
        "pipeline_events",
        {"select": "category,created_at,message", "stage": "eq.cardset", "status": "eq.failed",
         "channel": ch["channel_filter"], "created_at": f"gte.{start}", "order": "id.asc"},
    )
    grid = {c: {} for c in ch["categories"]}
    for f in failures:
        if f.get("category") in grid:
            grid[f["category"]][to_kst(f["created_at"]).date().isoformat()] = {"state": "failed", "title": f.get("message")}
    for c in cards:
        if c["category"] in grid:
            grid[c["category"]][to_kst(c["created_at"]).date().isoformat()] = {
                "state": "published" if c["published"] else "carded", "title": c["final_title"],
            }
    dates = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    tomorrow = today + timedelta(days=1)
    return {
        "dates": dates,
        "rows": [
            {
                "key": c,
                "label": CATEGORY_LABELS[c],
                "cells": [grid[c].get(d, {"state": "none"}) for d in dates],
                "published": sum(1 for v in grid[c].values() if v["state"] == "published"),
                # 하루 게시 수를 정한 채널(정보담당)은 이월 쿼터를 쓰지 않는다.
                "quota_tomorrow": None if ch["cfg"]["daily_posts"] else get_allowed_quota(c, as_of=tomorrow),
            }
            for c in ch["categories"]
        ],
        "max_backlog_days": MAX_BACKLOG_DAYS,
        "daily_posts": ch["cfg"]["daily_posts"],
    }


@app.get("/api/dash/cards", dependencies=[Depends(require_auth)])
async def dash_cards(day_param: str | None = Query(None, alias="date"), channel: str | None = None):
    ch = channel_ctx(channel)
    since, _ = kst_day_range(datetime.now(KST).date() - timedelta(days=29))
    recent = sb_get_all("cards", {"select": "created_at", "category": ch["category_filter"], "created_at": f"gte.{since}", "order": "created_at.desc"})
    available = sorted({to_kst(c["created_at"]).date().isoformat() for c in recent}, reverse=True)
    day = parse_day(day_param) if day_param else (date.fromisoformat(available[0]) if available else datetime.now(KST).date())
    start, end = kst_day_range(day)
    cards = sb_get_all(
        "cards",
        {"select": "id,category,final_title,image_path,published,created_at,news_item_id", "category": ch["category_filter"],
         "and": f"(created_at.gte.{start},created_at.lt.{end})", "order": "created_at.asc"},
    )
    news_ids = ",".join(str(c["news_item_id"]) for c in cards if c.get("news_item_id"))
    news = {n["id"]: n for n in sb_get("news_items", {"select": "id,title,source,url", "id": f"in.({news_ids})"})} if news_ids else {}
    return {
        "date": day.isoformat(),
        "available": available,
        "cards": [
            {
                "id": c["id"],
                "category": c["category"],
                "label": CATEGORY_LABELS.get(c["category"], c["category"]),
                "title": (news.get(c["news_item_id"]) or {}).get("title") or c["final_title"],
                "url": (news.get(c["news_item_id"]) or {}).get("url"),
                "published": c["published"],
                "time": to_kst(c["created_at"]).strftime("%H:%M"),
                "slides": list_slides(c["image_path"]),
            }
            for c in cards
        ],
    }


@app.get("/api/dash/overview", dependencies=[Depends(require_auth)])
async def dash_overview():
    """채널별 오늘 요약(오늘 탭 맨 위 채널 카드). 화면은 30초에 한 번만 부른다."""
    today = datetime.now(KST).date()
    start, end = kst_day_range(today)
    result = []
    for key in CHANNELS:
        ch = channel_ctx(key)
        cards = sb_get_all("cards", {"select": "published", "category": ch["category_filter"],
                                     "and": f"(created_at.gte.{start},created_at.lt.{end})"})
        selected = sb_count("news_items", {"status": "eq.approved", "category": ch["category_filter"],
                                           "and": f"(collected_at.gte.{start},collected_at.lt.{end})"})
        failed = sb_count("pipeline_events", {"channel": ch["channel_filter"], "status": "eq.failed",
                                              "created_at": f"gte.{start}"})
        runs = sb_get("pipeline_events", {"select": "status,created_at", "channel": ch["channel_filter"],
                                          "stage": "eq.run", "order": "created_at.desc", "limit": "1"})
        result.append({
            "key": key,
            "name": ch["cfg"]["name"],
            "target": ch["target"],
            "selected": selected,
            "carded": len(cards),
            "published": sum(1 for c in cards if c["published"]),
            "failed": failed,
            "last_run": {"status": runs[0]["status"], "when": to_kst(runs[0]["created_at"]).strftime("%m/%d %H:%M")}
            if runs else None,
        })
    return {"date": today.isoformat(), "channels": result}


@app.post("/api/index")
@app.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if TELEGRAM_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid secret token")

    update = await request.json()
    callback_query = update.get("callback_query")
    if not callback_query:
        return {"ok": True}

    callback_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    try:
        action, news_item_id_str = data.split(":", 1)
        news_item_id = int(news_item_id_str)
    except ValueError:
        tg_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "잘못된 요청입니다."})
        return {"ok": True}

    items = sb_get("news_items", {"select": "id,category,title,status", "id": f"eq.{news_item_id}"})
    if not items:
        tg_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "뉴스를 찾을 수 없습니다."})
        return {"ok": True}
    item = items[0]

    if action == "approve":
        if category_quota_reached(item["category"]):
            tg_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "이미 오늘 이 카테고리는 승인 가능 건수만큼 승인 완료되었습니다.",
                    "show_alert": True,
                },
            )
            return {"ok": True}

        decided_at = datetime.now(timezone.utc).isoformat()
        sb_patch("news_items", {"id": f"eq.{news_item_id}"}, {"status": "approved"})
        sb_patch(
            "approval_requests",
            {"news_item_id": f"eq.{news_item_id}"},
            {"decision": "approved", "decided_at": decided_at},
        )
        tg_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "승인되었습니다."})
        if chat_id and message_id:
            tg_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"✅ 승인됨\n{item['title']}",
                },
            )

    elif action == "reject":
        decided_at = datetime.now(timezone.utc).isoformat()
        sb_patch("news_items", {"id": f"eq.{news_item_id}"}, {"status": "rejected"})
        sb_patch(
            "approval_requests",
            {"news_item_id": f"eq.{news_item_id}"},
            {"decision": "rejected", "decided_at": decided_at},
        )
        tg_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "거절되었습니다."})
        if chat_id and message_id:
            tg_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"❌ 거절됨\n{item['title']}",
                },
            )
    else:
        tg_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "알 수 없는 동작입니다."})

    return {"ok": True}


# ---------------------------------------------------------------------------
# 화면 (단일 파일 배포를 위해 인라인). 색은 assets/templates/card-template-v2.html의
# 브랜드 토큰(--bg1 #EEEBF7 / --accent #7FA8D4 / --accent2 #A9D5C9 / --warn #EFA36B,
# 봄 시즌 #F3A9C0)을 다크 바탕 위 포인트색으로 옮겼다. 고양이 포즈는 Storage _brand/.
# ---------------------------------------------------------------------------

_BASE_CSS = r"""
:root{
  --sans:"Pretendard",-apple-system,system-ui,sans-serif;
  --bg:#15131B; --bg2:#1B1823; --surface:#221E2B; --surface2:#2A2535; --line:rgba(238,235,247,.08);
  --ink:#F2EFF8; --sub:#A39BB2; --faint:#6F6880;
  --lav:#C7B9F2; --sky:#7FA8D4; --mint:#A9D5C9; --peach:#EFA36B; --pink:#F3A9C0; --cream:#FFF4E2;
  --radius:20px; --shadow:0 10px 30px rgba(0,0,0,.25);
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:var(--bg);color:var(--ink);font-family:var(--sans);word-break:keep-all;overflow-wrap:anywhere}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{overflow-x:hidden;-webkit-tap-highlight-color:transparent}
body{min-height:100vh;background:radial-gradient(1200px 600px at 85% -10%,rgba(199,185,242,.10),transparent 60%),radial-gradient(900px 500px at -10% 110%,rgba(127,168,212,.08),transparent 60%),var(--bg)}
a{color:inherit}
button{font:inherit;color:inherit;cursor:pointer;border:0;background:none}
"""

LOGIN_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>묘한 경제 공장</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<style>""" + _BASE_CSS + r"""
.wrap{min-height:100vh;display:grid;place-items:center;padding:24px 16px}
.box{width:100%;max-width:360px;background:var(--surface);border:1px solid var(--line);border-radius:28px;padding:36px 28px;text-align:center;box-shadow:var(--shadow)}
.box img{width:112px;height:112px;object-fit:contain;margin-bottom:8px;background:var(--cream);border-radius:28px;padding:6px}
h1{font-size:22px;font-weight:800}
.box p{color:var(--sub);font-size:14px;margin:6px 0 24px}
input{width:100%;font:inherit;font-size:16px;color:var(--ink);background:var(--bg2);border:1px solid var(--line);border-radius:14px;padding:14px 16px;outline:none}
input:focus{border-color:var(--lav)}
.go{width:100%;margin-top:12px;padding:14px;border-radius:14px;background:var(--lav);color:#221E2B;font-weight:800;font-size:16px}
.err{color:var(--peach)!important;margin:12px 0 0!important}
</style></head><body><div class="wrap"><form class="box" method="post" action="/login">
<img src="{storage_base}_brand/cat-default.png" alt="">
<h1>묘한 경제 공장</h1><p>운영자 전용이에요</p>
<input type="password" name="password" placeholder="비밀번호" autocomplete="current-password" required autofocus>
<button class="go" type="submit">들어가기</button>{error}
</form></div></body></html>"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex"><meta name="theme-color" content="#15131B"><title>카드뉴스 공장</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<style>""" + _BASE_CSS + r"""
.app{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}
.side{position:sticky;top:0;height:100vh;padding:24px 16px;display:flex;flex-direction:column;gap:28px;background:rgba(27,24,35,.75);border-right:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;padding:0 6px}
.brand img{width:44px;height:44px;object-fit:contain;background:var(--cream);border-radius:14px;padding:3px}
.brand b{display:block;font-size:16px}.brand span{font-size:12px;color:var(--sub)}
.nav{display:flex;flex-direction:column;gap:4px}
.nav button{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:14px;color:var(--sub);font-weight:600;font-size:15px;text-align:left}
.nav button svg{width:20px;height:20px;flex-shrink:0}
.nav button:hover{background:var(--surface)}
.nav button.on{background:linear-gradient(90deg,rgba(199,185,242,.22),rgba(199,185,242,.06));color:var(--ink)}
.nav button.on svg{color:var(--lav)}
.side-foot{margin-top:auto;display:flex;flex-direction:column;gap:10px}
.live{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--sub);padding:10px 12px;background:var(--surface);border-radius:12px}
.live i{width:8px;height:8px;border-radius:50%;background:var(--mint);box-shadow:0 0 0 4px rgba(169,213,201,.15);flex-shrink:0}
.live.err i{background:var(--peach);box-shadow:0 0 0 4px rgba(239,163,107,.15)}
.out{font-size:13px;color:var(--faint);padding:8px 12px;text-align:left;width:100%}
main{min-width:0;padding:28px 32px 48px}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin-bottom:22px}
.top h1{font-size:26px;font-weight:800;letter-spacing:-.02em}
.top p{color:var(--sub);font-size:14px;margin-top:4px}
.top .live{display:none}
.view{display:none}.view.on{display:block}
.grid{display:grid;gap:16px}
.k4{grid-template-columns:repeat(4,minmax(0,1fr))}
.k3{grid-template-columns:repeat(3,minmax(0,1fr))}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;min-width:0}
.panel h2{font-size:16px;font-weight:700;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.panel h2 small{font-size:12px;color:var(--sub);font-weight:500}
.kpi{display:flex;flex-direction:column;gap:8px}
.kpi .ic{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;background:color-mix(in srgb,var(--c) 18%,transparent);color:var(--c)}
.kpi .ic svg{width:20px;height:20px}
.kpi span{font-size:13px;color:var(--sub)}
.kpi b{font-size:28px;font-weight:800;letter-spacing:-.02em}
.kpi b small{font-size:15px;color:var(--sub);font-weight:600}
.notice{display:flex;align-items:center;gap:12px;background:rgba(199,185,242,.10);border:1px solid rgba(199,185,242,.25);border-radius:16px;padding:12px 16px;font-size:14px;line-height:1.5;margin-bottom:16px}
.notice img{width:44px;height:44px;object-fit:contain;background:var(--cream);border-radius:12px;padding:2px;flex-shrink:0}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;padding:4px 10px;border-radius:999px;background:color-mix(in srgb,var(--c,var(--sub)) 18%,transparent);color:var(--c,var(--sub));white-space:nowrap}
.cat-card{display:flex;flex-direction:column;gap:12px;border-top:3px solid var(--c)}
.cc-head{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.cc-body{display:flex;gap:12px;align-items:flex-start}
.mascot{width:64px;height:64px;object-fit:contain;flex-shrink:0;background:var(--cream);border-radius:18px;padding:4px}
.cc-title{font-weight:700;font-size:15px;line-height:1.45;text-decoration:none;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
a.cc-title:hover{text-decoration:underline}
.cc-meta{font-size:12px;color:var(--sub);margin-top:4px}
.cc-reason{font-size:13px;color:var(--peach);background:rgba(239,163,107,.10);border-radius:12px;padding:10px 12px;line-height:1.5}
.cc-cover{width:100%;aspect-ratio:4/5;object-fit:cover;object-position:top;border-radius:14px;max-height:220px;background:var(--bg2)}
.feed{list-style:none;display:flex;flex-direction:column}
.feed li{display:grid;grid-template-columns:46px 12px minmax(0,1fr);gap:10px;align-items:start;padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}
.feed li:last-child{border-bottom:0}
.feed time{font-variant-numeric:tabular-nums;color:var(--sub);font-size:13px;padding-top:1px}
.feed .dot{width:10px;height:10px;border-radius:50%;margin-top:5px;background:var(--c)}
.feed .st{font-weight:700;margin-right:6px}
.feed .msg{color:var(--sub);line-height:1.5;margin-top:2px}
.empty{color:var(--faint);font-size:14px;padding:18px 0;text-align:center}
.datenav{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.datenav button,.seg button,select{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:9px 13px;font-size:14px;color:var(--ink);font-family:inherit;min-height:40px}
.datenav b{font-size:15px;min-width:96px;text-align:center}
.seg{display:flex;gap:6px;flex-wrap:wrap}
.seg button.on{background:var(--lav);color:#221E2B;border-color:var(--lav);font-weight:700}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.news{list-style:none}
.news li{padding:10px 0;border-bottom:1px solid var(--line)}
.news li:last-child{border-bottom:0}
.news a{font-size:14px;font-weight:600;text-decoration:none;line-height:1.45}
.news a:hover{text-decoration:underline}
.news .m{font-size:12px;color:var(--sub);margin-top:3px;display:flex;gap:8px;flex-wrap:wrap}
.picked{background:linear-gradient(135deg,rgba(199,185,242,.16),rgba(199,185,242,.04));border:1px solid rgba(199,185,242,.35);border-radius:14px;padding:12px 14px;margin-bottom:10px}
.picked .chip{margin-bottom:6px}
.kw{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) auto;gap:12px;align-items:center;padding:11px 0;border-bottom:1px solid var(--line);font-size:14px}
.kw:last-child{border-bottom:0}
.kw .name{font-weight:700;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bar{height:8px;border-radius:99px;background:var(--bg2);overflow:hidden}
.bar i{display:block;height:100%;border-radius:99px;background:var(--c,var(--lav))}
.kw .num{font-size:12px;color:var(--sub);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.flag{--c:var(--peach)}
.heat{display:grid;gap:4px;align-items:center}
.heat .lbl{font-size:12px;color:var(--sub);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell{height:26px;border-radius:6px;background:repeating-linear-gradient(135deg,rgba(238,235,247,.07) 0 3px,transparent 3px 6px);border:1px solid var(--line)}
.cell.published{background:var(--mint);border-color:transparent}
.cell.carded{background:var(--sky);border-color:transparent}
.cell.failed{background:var(--peach);border-color:transparent}
.cell.today{outline:2px solid var(--lav);outline-offset:1px}
.axis{font-size:11px;color:var(--faint);text-align:center;white-space:nowrap;overflow:visible}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--sub)}
.legend span{display:flex;align-items:center;gap:6px}
.legend i{width:12px;height:12px;border-radius:4px;display:inline-block}
.bal{display:grid;grid-template-columns:84px minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 0;font-size:14px}
.quota{display:flex;flex-direction:column;align-items:center;gap:4px;padding:14px 6px;border-radius:16px;background:var(--bg2)}
.quota b{font-size:26px}.quota span{font-size:12px;color:var(--sub)}
.set{margin-bottom:16px}
.slides{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:10px}
.slides img{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:12px;background:var(--bg2);cursor:zoom-in;display:block}
.lightbox{position:fixed;inset:0;background:rgba(10,9,14,.92);display:none;align-items:center;justify-content:center;z-index:50;padding:16px}
.lightbox.on{display:flex}
.lightbox img{max-width:100%;max-height:86vh;border-radius:14px}
.lb-btn{position:absolute;top:50%;transform:translateY(-50%);width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.12);font-size:22px}
.lb-prev{left:12px}.lb-next{right:12px}.lb-close{position:absolute;top:14px;right:14px;width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.12);font-size:20px}
.muted{color:var(--sub);font-size:13px;line-height:1.6}
.app{min-height:100dvh}
.top-r{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}
.chseg button{min-width:92px}
.chstrip{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:16px}
.chcard{text-align:left;display:flex;flex-direction:column;gap:10px;cursor:pointer;width:100%}
.chcard.on{border-color:var(--lav);box-shadow:inset 0 0 0 1px var(--lav)}
.chc-head{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.chc-head b{font-size:16px}
.chc-nums{display:flex;gap:14px;font-size:13px;color:var(--sub);flex-wrap:wrap}
.chc-nums b{color:var(--ink);font-size:17px;margin-left:3px}
.chc-run{font-size:12px;color:var(--faint)}
.heat-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain}
.heat .lbl,.heat>div:first-of-type{position:sticky;left:0;z-index:1;background:var(--surface);padding-right:6px}
body[data-ch="youth"] .mascot,body[data-ch="youth"] .notice img{display:none}
.tabbar{display:none}
@media (max-width:1100px){.k3{grid-template-columns:minmax(0,1fr)}.k4{grid-template-columns:repeat(2,minmax(0,1fr))}.split{grid-template-columns:minmax(0,1fr)!important}}
@media (max-width:820px){
  .app{grid-template-columns:minmax(0,1fr)}
  .side{display:none}
  main{padding:18px 16px calc(92px + env(safe-area-inset-bottom))}
  .top{align-items:center}.top h1{font-size:22px}.top p{font-size:13px}.top .live{display:flex;padding:6px 10px}
  .panel{padding:16px;border-radius:18px}
  .kpi b{font-size:24px}
  .tabbar{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));position:fixed;left:0;right:0;bottom:0;z-index:40;background:rgba(27,24,35,.96);border-top:1px solid var(--line);padding:6px 4px calc(6px + env(safe-area-inset-bottom));backdrop-filter:blur(10px)}
  .tabbar button{display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 0;min-height:52px;font-size:11px;color:var(--faint);font-weight:600}
  .tabbar button svg{width:22px;height:22px}
  .tabbar button.on{color:var(--lav)}
  .kw{grid-template-columns:minmax(0,1fr) auto}.kw .bar{grid-column:1/-1;order:3}
  .heat{gap:2px}.cell{height:22px;border-radius:4px}.heat-wrap{--lw:52px!important}
  .axis{font-size:10px}
  .slides{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
  .slides img{border-radius:8px}
  .top{flex-wrap:wrap}.top-r{width:100%;justify-content:space-between}
  .chseg{flex:1}.chseg button{flex:1;min-width:0}
  .heat{min-width:560px}
}
/* 갤럭시 S 시리즈(가로 360~412px)·폴드 커버 화면(344px) */
@media (max-width:440px){
  main{padding:14px 12px calc(88px + env(safe-area-inset-bottom))}
  .grid{gap:10px}
  .panel{padding:14px;border-radius:16px}
  .top{margin-bottom:14px}.top h1{font-size:20px}
  .kpi{gap:6px}.kpi .ic{width:32px;height:32px;border-radius:10px}.kpi .ic svg{width:18px;height:18px}
  .kpi b{font-size:21px}.kpi span{font-size:12px}
  .chstrip{gap:8px;margin-bottom:12px}
  .chcard{padding:12px;gap:8px}.chc-head b{font-size:14px}.chc-nums{gap:8px;font-size:12px}.chc-nums b{font-size:15px}
  .feed li{grid-template-columns:40px 10px minmax(0,1fr);gap:8px;font-size:13px}
  .datenav{gap:6px}.datenav b{min-width:84px;font-size:14px}
  .datenav button,.seg button,select{padding:8px 10px;font-size:13px}
  .cc-cover{max-height:180px}
  .mascot{width:52px;height:52px;border-radius:14px}
  .bal{grid-template-columns:minmax(0,1fr) auto;gap:6px 8px}.bal .bar{grid-column:1/-1;order:3}.bal .chip{justify-self:start}
  .heat-wrap{--lw:64px!important}
  .lb-btn{display:none}
}
@media (max-width:360px){
  .chstrip{grid-template-columns:minmax(0,1fr)}
  .k4{grid-template-columns:repeat(2,minmax(0,1fr))}
  .tabbar button{font-size:10px}
}
</style></head><body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-today" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4"/></symbol>
<symbol id="i-topics" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/></symbol>
<symbol id="i-keywords" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/></symbol>
<symbol id="i-calendar" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/></symbol>
<symbol id="i-cards" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="13" height="15" rx="2.5"/><path d="M8 3h10a3 3 0 0 1 3 3v11"/></symbol>
<symbol id="i-collect" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16l-6 8v6l-4 2v-8z"/></symbol>
<symbol id="i-pick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></symbol>
<symbol id="i-send" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/></symbol>
<symbol id="i-flag" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 21V4h11l-2 4 2 4H5"/></symbol>
</defs></svg>
<div class="app">
  <aside class="side">
    <div class="brand"><img data-cat="cat-default" alt=""><div><b id="brandName">묘한 경제</b><span>카드뉴스 공장</span></div></div>
    <nav class="nav" id="sideNav"></nav>
    <div class="side-foot">
      <div class="live" id="live1"><i></i><span>연결 중…</span></div>
      <form method="post" action="/logout"><button class="out" type="submit">로그아웃</button></form>
    </div>
  </aside>
  <main>
    <header class="top"><div><h1 id="title">오늘</h1><p id="subtitle"></p></div><div class="top-r"><div class="seg chseg" id="chSeg" role="tablist" aria-label="채널"></div><div class="live" id="live2"><i></i><span>연결 중…</span></div></div></header>
    <section class="view" id="v-today"></section>
    <section class="view" id="v-topics"></section>
    <section class="view" id="v-keywords"></section>
    <section class="view" id="v-calendar"></section>
    <section class="view" id="v-cards"></section>
  </main>
</div>
<nav class="tabbar" id="tabBar"></nav>
<div class="lightbox" id="lb"><button class="lb-close" id="lbClose" aria-label="닫기">✕</button><button class="lb-btn lb-prev" id="lbPrev" aria-label="이전">‹</button><img id="lbImg" alt=""><button class="lb-btn lb-next" id="lbNext" aria-label="다음">›</button></div>
<script>
const STORAGE = "{storage_base}";
const POLL_MS = 4000;
const CHANNELS = {channels_json};
const CH_COLORS = ["var(--sky)", "var(--pink)", "var(--mint)", "var(--peach)", "var(--lav)", "var(--cream)"];
let CH = new URLSearchParams(location.search).get("ch") || localStorage.getItem("mh_channel") || "economy";
if (!CHANNELS[CH]) CH = Object.keys(CHANNELS)[0];
const OV_MS = 30000;
let ovCache = null, ovAt = 0;
const TABS = [
  ["today","오늘","실시간 파이프라인 현황"],["topics","주제","자동 선정된 기사와 후보 풀"],
  ["keywords","키워드","검색어별 수집 대비 선정률"],["calendar","캘린더","최근 30일 게시 기록과 발행 균형"],
  ["cards","카드","생성된 캐러셀 미리보기"],
];
const CAT = {};  // 모든 채널의 카테고리 → 이름·색(경제는 기존 색 그대로)
Object.values(CHANNELS).forEach((ch, ci) => Object.entries(ch.categories).forEach(([k, label], i) => { CAT[k] = {label, c: CH_COLORS[(ci * 3 + i) % CH_COLORS.length]}; }));
const STATE = {
  published:["게시됨","var(--mint)","cat-cheer"], publishing:["게시 중","var(--lav)","cat-money"],
  carded:["카드 생성됨","var(--sky)","cat-happy"], making:["카드 만드는 중","var(--lav)","cat-thinking"],
  selected:["선정됨","var(--lav)","cat-magnify"], failed:["실패","var(--peach)","cat-worried"],
  empty:["후보 없음","var(--sub)","cat-melting"], waiting:["대기","var(--sub)","cat-sit"],
};
const STAGE = {run:"실행",collect:"수집",clean:"정제",select:"자동 선정",cardset:"카드 생성",publish:"게시",keyword_stats:"키워드 집계"};
const EV = {started:["시작","var(--lav)"],success:["완료","var(--mint)"],retry:["재시도","var(--peach)"],failed:["실패","var(--peach)"],skipped:["건너뜀","var(--sub)"]};

const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const safeUrl = u => /^https?:\/\//i.test(u || "") ? esc(u) : "#";
const catImg = n => STORAGE + "_brand/" + n + ".png";
const fmt = n => n == null ? "–" : Number(n).toLocaleString("ko-KR");
const catChip = k => `<span class="chip" style="--c:${(CAT[k]||{}).c||"var(--sub)"}">${esc((CAT[k]||{}).label||k||"전체")}</span>`;
const kstToday = () => new Date(Date.now() + 9*3600e3).toISOString().slice(0,10);
const shiftDay = (d, n) => { const t = new Date(d + "T00:00:00Z"); t.setUTCDate(t.getUTCDate()+n); return t.toISOString().slice(0,10); };
document.querySelectorAll("img[data-cat]").forEach(i => i.src = catImg(i.dataset.cat));

let tab = "today", timer = null, topicsDay = null, cardsDay = null, kwFilter = "all", kwCat = "all", kwData = null, lbList = [], lbIdx = 0;

const navHtml = () => TABS.map(([k,l]) => `<button data-tab="${k}"><svg><use href="#i-${k}"/></svg><span>${l}</span></button>`).join("");
$("#chSeg").innerHTML = Object.entries(CHANNELS).map(([k, c]) => `<button data-ch="${esc(k)}" role="tab">${esc(c.name)}</button>`).join("");
document.querySelectorAll("[data-ch]").forEach(b => b.onclick = () => setChannel(b.dataset.ch));
function paintChannel() {
  document.querySelectorAll("[data-ch]").forEach(b => { b.classList.toggle("on", b.dataset.ch === CH); b.setAttribute("aria-selected", b.dataset.ch === CH); });
  $("#brandName").textContent = CHANNELS[CH].name;
  document.body.dataset.ch = CH;
  document.title = CHANNELS[CH].name + " · 카드뉴스 공장";
}
function setChannel(k) {
  if (!CHANNELS[k] || k === CH) return;
  CH = k; localStorage.setItem("mh_channel", k);
  topicsDay = null; cardsDay = null; kwData = null; kwCat = "all"; kwFilter = "all";
  paintChannel(); refresh();
}
$("#sideNav").innerHTML = navHtml(); $("#tabBar").innerHTML = navHtml();
document.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => { location.hash = b.dataset.tab; });

function setLive(ok, text) {
  ["#live1","#live2"].forEach(id => { const el = $(id); el.classList.toggle("err", !ok); el.querySelector("span").textContent = text; });
}

async function api(path) {
  path += (path.includes("?") ? "&" : "?") + "channel=" + encodeURIComponent(CH);
  const r = await fetch(path, {cache: "no-store", headers: {"Accept": "application/json"}});
  if (r.status === 401) { location.reload(); throw new Error("login"); }
  if (!r.ok) throw new Error(r.status + " " + (await r.text()).slice(0, 120));
  return r.json();
}

function show(next) {
  tab = TABS.some(t => t[0] === next) ? next : "today";
  const meta = TABS.find(t => t[0] === tab);
  $("#title").textContent = meta[1]; $("#subtitle").textContent = meta[2];
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + tab));
  document.querySelectorAll("[data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  refresh();
}

async function refresh() {
  clearTimeout(timer);
  const fn = {today: loadToday, topics: loadTopics, keywords: loadKeywords, calendar: loadCalendar, cards: loadCards}[tab];
  try {
    await fn();
    const t = new Date().toTimeString().slice(0, 8);
    setLive(true, tab === "today" ? `실시간 · ${t}` : `갱신 ${t}`);
  } catch (e) {
    if (e.message !== "login") setLive(false, "연결 실패 · 재시도 중");
  }
  if (tab === "today" && document.visibilityState === "visible") timer = setTimeout(refresh, POLL_MS);
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); else clearTimeout(timer); });
window.addEventListener("hashchange", () => show(location.hash.slice(1)));

function notice(pose, html) {
  return `<div class="notice"><img src="${catImg(pose)}" alt=""><div>${html}</div></div>`;
}
function kpiTile(label, value, icon, color, sub) {
  return `<div class="panel kpi" style="--c:${color}"><div class="ic"><svg><use href="#i-${icon}"/></svg></div><span>${label}</span><b>${value}</b>${sub ? `<span>${sub}</span>` : ""}</div>`;
}

/* ---------- 오늘 ---------- */
async function loadOverview() {
  if (ovCache && Date.now() - ovAt < OV_MS) return ovCache;
  try { ovCache = await api("/api/dash/overview"); ovAt = Date.now(); } catch (e) { if (e.message === "login") throw e; }
  return ovCache;
}
function overviewStrip(ov) {
  if (!ov || !ov.channels || ov.channels.length < 2) return "";
  return `<div class="chstrip">${ov.channels.map(c => {
    const run = c.last_run ? `${EV[c.last_run.status] ? EV[c.last_run.status][0] : esc(c.last_run.status)} · ${esc(c.last_run.when)}` : "기록 없음";
    const color = c.published >= c.target ? "var(--mint)" : (c.failed ? "var(--peach)" : "var(--lav)");
    return `<button class="panel chcard${c.key === CH ? " on" : ""}" data-chgo="${esc(c.key)}" aria-label="${esc(c.name)} 보기">
      <div class="chc-head"><b>${esc(c.name)}</b><span class="chip" style="--c:${color}">게시 ${c.published}/${c.target}</span></div>
      <div class="chc-nums"><span>선정<b>${c.selected}</b></span><span>카드<b>${c.carded}</b></span><span>실패<b>${c.failed}</b></span></div>
      <div class="chc-run">마지막 실행 ${run}</div></button>`;
  }).join("")}</div>`;
}
async function loadToday() {
  const [d, ov] = await Promise.all([api("/api/dash/today"), loadOverview()]);
  const k = d.kpi;
  const top = d.showing === "latest_run"
    ? notice("cat-sit", `오늘은 아직 실행 전이에요. 가장 최근 실행(<b>${esc(d.day)}</b>) 기록을 보여드려요.`)
    : (!d.timeline.length ? notice("cat-sit", "아직 기록된 실행이 없어요. 다음 실행부터 단계별로 여기에 쌓여요.") : "");
  const run = d.run ? `${STAGE.run} ${EV[d.run.status] ? EV[d.run.status][0] : esc(d.run.status)} · ${esc(d.run.time)}${d.run.trigger ? " · " + esc(d.run.trigger) : ""}` : "실행 기록 없음";
  const cats = d.categories.map(c => {
    const [label, color, pose] = STATE[c.state] || STATE.waiting;
    const title = c.news ? `<a class="cc-title" href="${safeUrl(c.news.url)}" target="_blank" rel="noopener">${esc(c.news.title)}</a>` : `<span class="cc-title" style="color:var(--faint)">아직 선정된 기사가 없어요</span>`;
    const meta = [c.news && c.news.source, c.retries ? `재시도 ${c.retries}회` : ""].filter(Boolean).map(esc).join(" · ");
    return `<article class="panel cat-card" style="--c:${CAT[c.key].c}">
      <div class="cc-head">${catChip(c.key)}<span class="chip" style="--c:${color}">${label}</span></div>
      <div class="cc-body"><img class="mascot" src="${catImg(pose)}" alt=""><div style="min-width:0">${title}${meta ? `<p class="cc-meta">${meta}</p>` : ""}</div></div>
      ${c.reason ? `<p class="cc-reason">${esc(c.reason)}</p>` : ""}
      ${c.card ? `<img class="cc-cover" loading="lazy" src="${safeUrl(c.card.cover)}" alt="표지" onerror="this.remove()">` : ""}
    </article>`;
  }).join("");
  const feed = d.timeline.length ? `<ul class="feed">${d.timeline.slice().reverse().map(e => {
    const [st, color] = EV[e.status] || [e.status, "var(--sub)"];
    return `<li style="--c:${color}"><time>${esc(e.time.slice(0,5))}</time><span class="dot"></span><div><span class="st">${esc((STAGE[e.stage] || e.stage) + " " + st)}</span>${e.category ? catChip(e.category) : ""}<div class="msg">${esc(e.message || "")}</div></div></li>`;
  }).join("")}</ul>` : `<p class="empty">기록 없음</p>`;
  $("#v-today").innerHTML = `${overviewStrip(ov)}${top}
    <div class="grid k4" style="margin-bottom:16px">
      ${kpiTile("수집", fmt(k.collected), "collect", "var(--sky)", "Google News RSS")}
      ${kpiTile("정제", fmt(k.cleaned), "topics", "var(--lav)", "중복 제거 후 후보")}
      ${kpiTile("선정", `${k.selected}<small> / ${k.target}</small>`, "pick", "var(--pink)", CHANNELS[CH].daily_posts ? `하루 1~${CHANNELS[CH].daily_posts}건` : "카테고리별 1건")}
      ${kpiTile("게시", `${k.published}<small> / ${k.target}</small>`, "send", "var(--mint)", "인스타그램")}
    </div>
    <div class="grid k3" style="margin-bottom:16px">${cats}</div>
    <div class="panel"><h2>타임라인 <small>${run}</small></h2>${feed}</div>`;
  document.querySelectorAll("[data-chgo]").forEach(b => b.onclick = () => setChannel(b.dataset.chgo));
}

/* ---------- 주제 ---------- */
async function loadTopics() {
  topicsDay = topicsDay || kstToday();
  const d = await api("/api/dash/topics?date=" + encodeURIComponent(topicsDay));
  const when = s => s ? new Date(s).toLocaleString("ko-KR", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false}) : "";
  const panels = d.categories.map(c => {
    const picked = c.selected.map(s => `<div class="picked"><span class="chip" style="--c:var(--lav)">선정</span>
      <ul class="news"><li style="padding:0;border:0"><a href="${safeUrl(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>
      <div class="m"><span>${esc(s.source || "")}</span>${s.keyword ? `<span>#${esc(s.keyword)}</span>` : ""}</div></li></ul></div>`).join("");
    const pool = c.candidates.filter(x => x.status !== "approved").map(x => `<li><a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
      <div class="m"><span>${esc(x.source || "")}</span><span>${esc(when(x.published_at))}</span>${x.keyword ? `<span>#${esc(x.keyword)}</span>` : ""}</div></li>`).join("");
    return `<div class="panel" style="border-top:3px solid ${CAT[c.key].c}"><h2>${catChip(c.key)}<small>후보 ${fmt(c.total)}건 중 최신 ${c.candidates.length}건</small></h2>
      ${picked || `<p class="muted" style="margin-bottom:8px">선정된 기사가 없어요</p>`}
      ${pool ? `<ul class="news">${pool}</ul>` : `<p class="empty">후보 없음</p>`}</div>`;
  }).join("");
  $("#v-topics").innerHTML = `<div class="toolbar"><div class="datenav"><button id="tPrev" aria-label="전날">‹</button><b>${esc(d.date)}</b><button id="tNext" aria-label="다음날">›</button><button id="tToday">오늘</button></div></div>
    <div class="grid k3">${panels}</div>`;
  $("#tPrev").onclick = () => { topicsDay = shiftDay(topicsDay, -1); refresh(); };
  $("#tNext").onclick = () => { topicsDay = shiftDay(topicsDay, 1); refresh(); };
  $("#tToday").onclick = () => { topicsDay = kstToday(); refresh(); };
}

/* ---------- 키워드 성과 ---------- */
async function loadKeywords() {
  kwData = await api("/api/dash/keywords");
  renderKeywords();
}
function renderKeywords() {
  const d = kwData;
  if (!d.rows.length) {
    $("#v-keywords").innerHTML = notice("cat-thinking", "아직 집계가 없어요. 다음 auto 실행이 끝나면 최근 30일 기준으로 채워져요.");
    return;
  }
  const rate = r => r.collected ? r.approved / r.collected : 0;
  let rows = d.rows.slice().sort((a, b) => rate(b) - rate(a) || b.collected - a.collected);
  if (kwCat !== "all") rows = rows.filter(r => r.category === kwCat);
  if (kwFilter === "flag") rows = rows.filter(r => r.replace);
  const flagged = d.rows.filter(r => r.replace).length;
  const total = d.rows.reduce((s, r) => s + r.collected, 0), sel = d.rows.reduce((s, r) => s + r.approved, 0);
  const maxRate = Math.max(...d.rows.map(rate), 0.0001);
  $("#v-keywords").innerHTML = `
    <div class="grid k4" style="margin-bottom:16px">
      ${kpiTile("검색어", d.rows.length, "keywords", "var(--lav)", `최근 ${d.days}일 · ${esc(d.updated)}`)}
      ${kpiTile("수집", fmt(total), "collect", "var(--sky)", "기간 내 전체")}
      ${kpiTile("선정", fmt(sel), "pick", "var(--mint)", `선정률 ${(sel / Math.max(total, 1) * 100).toFixed(2)}%`)}
      ${kpiTile("교체 검토", flagged, "flag", "var(--peach)", `선정 0건·수집 ${d.min_collected}건↑ 또는 수집 0건`)}
    </div>
    <div class="toolbar">
      <div class="seg" id="kwF"><button data-v="all">전체</button><button data-v="flag">교체 검토만</button></div>
      <div class="seg" id="kwC"><button data-v="all">모든 분야</button>${Object.entries(CHANNELS[CH].categories).map(([k, label]) => `<button data-v="${esc(k)}">${esc(label)}</button>`).join("")}</div>
    </div>
    <div class="panel">${rows.map(r => `<div class="kw">
      <div class="name">${esc(r.keyword)} ${catChip(r.category)}${r.replace ? `<span class="chip flag">교체 검토</span>` : ""}</div>
      <div class="bar" style="--c:${(CAT[r.category] || {}).c || "var(--lav)"}"><i style="width:${(rate(r) / maxRate * 100).toFixed(1)}%"></i></div>
      <div class="num">선정 ${fmt(r.approved)} / 수집 ${fmt(r.collected)} · ${(rate(r) * 100).toFixed(2)}%</div></div>`).join("") || `<p class="empty">해당 검색어 없음</p>`}</div>`;
  document.querySelectorAll("#kwF button").forEach(b => { b.classList.toggle("on", b.dataset.v === kwFilter); b.onclick = () => { kwFilter = b.dataset.v; renderKeywords(); }; });
  document.querySelectorAll("#kwC button").forEach(b => { b.classList.toggle("on", b.dataset.v === kwCat); b.onclick = () => { kwCat = b.dataset.v; renderKeywords(); }; });
}

/* ---------- 캘린더 ---------- */
async function loadCalendar() {
  const d = await api("/api/dash/calendar");
  const n = d.dates.length, today = kstToday();
  const STL = {published: "게시됨", carded: "카드만 생성(미게시)", failed: "QA 실패", none: "없음"};
  const heat = d.rows.map(r => `<div class="lbl">${esc(r.label)}</div>` + r.cells.map((c, i) =>
    `<div class="cell ${c.state}${d.dates[i] === today ? " today" : ""}" title="${esc(d.dates[i])} · ${esc(r.label)} · ${STL[c.state]}${c.title ? " · " + esc(c.title) : ""}"></div>`).join("")).join("");
  const axis = `<div></div>` + d.dates.map((x, i) => `<div class="axis">${(i % 7 === 0 && n - 1 - i >= 4) || i === n - 1 ? esc(+x.slice(5, 7) + "/" + +x.slice(8)) : ""}</div>`).join("");
  const maxPub = Math.max(...d.rows.map(r => r.published), 1);
  $("#v-calendar").innerHTML = `
    <div class="panel heat-wrap" style="margin-bottom:16px;--lw:84px"><h2>날짜 × 카테고리 <small>${esc(d.dates[0])} ~ ${esc(d.dates[n - 1])}</small></h2>
      <div class="legend" style="margin-bottom:14px"><span><i style="background:var(--mint)"></i>게시됨</span><span><i style="background:var(--sky)"></i>카드만 생성</span><span><i style="background:var(--peach)"></i>QA 실패</span><span><i class="cell" style="height:12px"></i>없음</span></div>
      <div class="heat-scroll" id="heatScroll"><div class="heat" style="grid-template-columns:var(--lw) repeat(${n},minmax(0,1fr))">${heat}${axis}</div></div></div>
    <div class="grid split" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)">
      <div class="panel"><h2>카테고리별 발행 균형 <small>최근 ${n}일 게시 수</small></h2>
        ${d.rows.map(r => `<div class="bal">${catChip(r.key)}<div class="bar" style="--c:${CAT[r.key].c}"><i style="width:${(r.published / maxPub * 100).toFixed(1)}%"></i></div><b>${r.published}<small style="color:var(--sub);font-weight:500"> / ${n}</small></b></div>`).join("")}</div>
      ${d.daily_posts ? `<div class="panel"><h2>하루 게시 목표 <small>${esc(CHANNELS[CH].name)}</small></h2>
        <div class="quota"><b>1~${d.daily_posts}</b><span>건 / 일</span></div>
        <p class="muted" style="margin-top:12px">세 분야 후보를 합쳐 좋은 기사가 있으면 하루 최대 ${d.daily_posts}건, 하나뿐이면 1건만 게시해요. 이월 쿼터는 쓰지 않아요.</p></div>`
      : `<div class="panel"><h2>내일 이월 쿼터 <small>get_allowed_quota()</small></h2>
        <div class="grid" style="grid-template-columns:repeat(3,minmax(0,1fr));gap:8px">${d.rows.map(r => `<div class="quota">${catChip(r.key)}<b>${r.quota_tomorrow}</b><span>건</span></div>`).join("")}</div>
        <p class="muted" style="margin-top:12px">발행이 빠진 연속 일수만큼(최대 ${d.max_backlog_days}일) 허용 건수가 늘어나요. 지금 auto 모드는 카테고리당 하루 1건만 선정하므로 참고 지표예요.</p></div>`}
    </div>`;
  const hs = $("#heatScroll"); if (hs) hs.scrollLeft = hs.scrollWidth;  // 폰에서는 오늘 쪽이 먼저 보이게
}

/* ---------- 카드 ---------- */
async function loadCards() {
  const d = await api("/api/dash/cards" + (cardsDay ? "?date=" + encodeURIComponent(cardsDay) : ""));
  cardsDay = d.date;
  const days = d.available.includes(d.date) ? d.available : [d.date, ...d.available];
  const opts = days.map(x => `<option value="${esc(x)}"${x === d.date ? " selected" : ""}>${esc(x)}</option>`).join("");
  const sets = d.cards.map(c => `<div class="panel set" style="border-top:3px solid ${(CAT[c.category] || {}).c || "var(--sub)"}">
      <h2><span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;min-width:0">${catChip(c.category)}<span style="min-width:0">${c.url ? `<a href="${safeUrl(c.url)}" target="_blank" rel="noopener" style="text-decoration:none">${esc(c.title)}</a>` : esc(c.title)}</span></span>
      <span class="chip" style="--c:${c.published ? "var(--mint)" : "var(--sky)"}">${c.published ? "게시됨" : "미게시"} · ${esc(c.time)}</span></h2>
      ${c.slides.length ? `<div class="slides">${c.slides.map((s, i) => `<img loading="lazy" src="${safeUrl(s)}" alt="${i + 1}번째 카드" data-set="${c.id}" data-i="${i}">`).join("")}</div>` : `<p class="muted">스토리지에 이미지가 없어요 (게시 전에 로컬에서만 만들어진 카드세트예요)</p>`}
    </div>`).join("");
  $("#v-cards").innerHTML = `<div class="toolbar"><div class="datenav"><button id="cPrev" aria-label="이전 날짜">‹</button><select id="cDay" aria-label="날짜">${opts}</select><button id="cNext" aria-label="다음 날짜">›</button><span class="muted">${d.cards.length}세트</span></div></div>
    ${sets || notice("cat-sit", "이 날짜에는 만들어진 카드세트가 없어요.")}`;
  const idx = days.indexOf(d.date);
  $("#cDay").onchange = e => { cardsDay = e.target.value; refresh(); };
  $("#cPrev").onclick = () => { if (idx < days.length - 1) { cardsDay = days[idx + 1]; refresh(); } };
  $("#cNext").onclick = () => { if (idx > 0) { cardsDay = days[idx - 1]; refresh(); } };
  document.querySelectorAll(".slides img").forEach(img => img.onclick = () => {
    lbList = [...document.querySelectorAll(`.slides img[data-set="${img.dataset.set}"]`)].map(x => x.src);
    lbIdx = +img.dataset.i; openLb();
  });
}
function openLb() { $("#lbImg").src = lbList[lbIdx]; $("#lb").classList.add("on"); }
const closeLb = () => $("#lb").classList.remove("on");
$("#lbClose").onclick = closeLb;
$("#lb").onclick = e => { if (e.target.id === "lb") closeLb(); };
$("#lbPrev").onclick = () => { lbIdx = (lbIdx - 1 + lbList.length) % lbList.length; openLb(); };
$("#lbNext").onclick = () => { lbIdx = (lbIdx + 1) % lbList.length; openLb(); };
document.addEventListener("keydown", e => {
  if (!$("#lb").classList.contains("on")) return;
  if (e.key === "Escape") closeLb();
  if (e.key === "ArrowLeft") $("#lbPrev").click();
  if (e.key === "ArrowRight") $("#lbNext").click();
});

let touchX = null;  // 폰에서 미리보기를 좌우로 밀어 넘기기
$("#lb").addEventListener("touchstart", e => { touchX = e.touches[0].clientX; }, {passive: true});
$("#lb").addEventListener("touchend", e => {
  if (touchX == null) return;
  const dx = e.changedTouches[0].clientX - touchX; touchX = null;
  if (Math.abs(dx) > 40) (dx < 0 ? $("#lbNext") : $("#lbPrev")).click();
});

paintChannel();
show(location.hash.slice(1) || "today");
</script></body></html>"""

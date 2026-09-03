"""텔레그램 승인 웹훅 (Vercel Serverless Function, FastAPI).

Vercel 배포 시 이 파일 하나만 번들되므로, 루트 프로젝트의 config.py/db.py를
재사용하지 않고 Supabase REST API를 httpx로 직접 호출하는 독립 모듈로 구성한다.

필요 환경변수 (Vercel 프로젝트 설정 > Environment Variables):
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

app = FastAPI()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
MAX_BACKLOG_DAYS = int(os.environ.get("MAX_BACKLOG_DAYS", 3))

KST = timezone(timedelta(hours=9))


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


def get_allowed_quota(category: str) -> int:
    """이월 큐 로직: 최근 카드 발행이 없었던 연속 일수만큼 오늘 승인 가능
    개수를 늘려준다. generate_cardset.py가 만드는 cards 테이블 기준(REST 버전)."""
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
    day = datetime.now(KST).date() - timedelta(days=1)
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


@app.get("/")
async def health():
    return {"status": "ok"}


CATEGORY_LABELS = {"index_macro": "지수/거시", "stock": "개별종목", "policy_industry": "정책/산업"}


@app.get("/status")
async def status_page():
    """오늘의 파이프라인 진행 상황을 보여주는 최소 대시보드(텔레그램 병행용)."""
    start = today_start_utc_iso()
    cards = sb_get("cards", {"select": "category,final_title,published", "created_at": f"gte.{start}"})
    approved = sb_get(
        "news_items",
        {"select": "category,title", "status": "eq.approved", "collected_at": f"gte.{start}"},
    )

    rows_html = ""
    for category in ("index_macro", "stock", "policy_industry"):
        cat_cards = [c for c in cards if c["category"] == category]
        cat_approved = [a for a in approved if a["category"] == category]
        if cat_cards:
            state = "✅ 카드 완성" + (" (인스타 업로드됨)" if all(c["published"] for c in cat_cards) else "")
            title = ", ".join(c["final_title"] for c in cat_cards)
        elif cat_approved:
            state = "🟡 승인됨, 카드 생성 대기"
            title = ", ".join(a["title"] for a in cat_approved)
        else:
            state = "⏳ 승인 대기중"
            title = "-"
        label = CATEGORY_LABELS.get(category, category)
        rows_html += f"<tr><td>{label}</td><td>{state}</td><td>{title}</td></tr>"

    html = f"""
    <html><head><meta charset="utf-8"><title>카드뉴스 오늘 현황</title>
    <style>body{{font-family:sans-serif;padding:24px}}table{{border-collapse:collapse;width:100%}}
    td,th{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f4f4f4}}</style>
    </head><body>
    <h2>오늘의 카드뉴스 진행 상황</h2>
    <table><tr><th>카테고리</th><th>상태</th><th>제목</th></tr>{rows_html}</table>
    </body></html>
    """
    return HTMLResponse(html)


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

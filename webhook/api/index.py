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

app = FastAPI()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

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


def category_already_approved_today(category: str) -> bool:
    rows = sb_get(
        "news_items",
        {
            "select": "id",
            "category": f"eq.{category}",
            "status": "eq.approved",
            "collected_at": f"gte.{today_start_utc_iso()}",
        },
    )
    return len(rows) > 0


@app.get("/")
async def health():
    return {"status": "ok"}


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
        if category_already_approved_today(item["category"]):
            tg_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "이미 오늘 이 카테고리는 승인 완료되었습니다.",
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

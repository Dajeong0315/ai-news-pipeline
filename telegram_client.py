"""Telegram Bot API 얇은 래퍼 (httpx 기반, python-telegram-bot 미사용)."""

import httpx

import config

API_BASE = "https://api.telegram.org/bot{token}"


def _url(method: str) -> str:
    return f"{API_BASE.format(token=config.TELEGRAM_BOT_TOKEN)}/{method}"


def send_message(text: str, reply_markup: dict | None = None, chat_id: str | None = None) -> dict:
    payload = {
        "chat_id": chat_id or config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = httpx.post(_url("sendMessage"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = httpx.post(_url("editMessageText"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False) -> dict:
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert,
    }
    resp = httpx.post(_url("answerCallbackQuery"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def approval_keyboard(news_item_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ 승인", "callback_data": f"approve:{news_item_id}"},
                {"text": "❌ 거절", "callback_data": f"reject:{news_item_id}"},
            ]
        ]
    }


def set_webhook(url: str) -> dict:
    payload = {"url": url}
    if config.TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = config.TELEGRAM_WEBHOOK_SECRET
    resp = httpx.post(_url("setWebhook"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()

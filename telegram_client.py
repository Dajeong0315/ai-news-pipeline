"""Telegram Bot API 얇은 래퍼 (httpx 기반, python-telegram-bot 미사용)."""

import json

import httpx

import config

API_BASE = "https://api.telegram.org/bot{token}"


def _url(method: str) -> str:
    return f"{API_BASE.format(token=config.TELEGRAM_BOT_TOKEN)}/{method}"


def send_message(text: str, reply_markup: dict | None = None, chat_id: str | None = None) -> dict:
    if config.CHANNEL != config.DEFAULT_CHANNEL:  # 봇 하나를 채널끼리 같이 쓰므로 어느 계정 알림인지 붙인다
        text = f"[{config.CHANNEL_NAME}] {text}"
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


def send_media_group(paths: list[str], chat_id: str | None = None) -> dict:
    """카드 이미지 묶음(최대 10장)을 앨범 하나로 보낸다. 로컬 파일을 그대로 올린다
    (Storage 공개 URL은 다시 만든 카드가 같은 경로라 텔레그램·CDN 캐시에 옛 그림이 남을 수 있다)."""
    media, files = [], {}
    for i, path in enumerate(paths[:10]):
        key = f"card{i}"
        media.append({"type": "photo", "media": f"attach://{key}"})
        files[key] = (f"{i + 1:02d}.png", open(path, "rb"), "image/png")
    try:
        resp = httpx.post(_url("sendMediaGroup"), data={"chat_id": chat_id or config.TELEGRAM_CHAT_ID,
                                                       "media": json.dumps(media)}, files=files, timeout=60)
    finally:
        for _, f, _ in files.values():
            f.close()
    resp.raise_for_status()
    return resp.json()


def card_keyboard(card_id: int) -> dict:
    """디징 카드 승인 버튼(2026-11-02). 콜백은 webhook/api/index.py의 card: 처리."""
    return {
        "inline_keyboard": [[
            {"text": "✅ 게시", "callback_data": f"card:approve:{card_id}"},
            {"text": "🔁 다시 만들기", "callback_data": f"card:regenerate:{card_id}"},
            {"text": "⏭ 건너뛰기", "callback_data": f"card:reject:{card_id}"},
        ]]
    }


def set_webhook(url: str) -> dict:
    payload = {"url": url}
    if config.TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = config.TELEGRAM_WEBHOOK_SECRET
    resp = httpx.post(_url("setWebhook"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()

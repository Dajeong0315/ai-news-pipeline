"""18:30 승인 모아보기(Phase 5 전날 제작, 2026-11-14).

17:30에 만든 내일 카드·예비분의 승인 상태를 한 번에 알리고, 아직 승인 안 한 카드는 버튼을 다시 붙여 보낸다.
게시 시각까지 승인 안 된 자리는 예비분이 대신 올라간다는 것도 알린다. nextday가 꺼진 채널은 아무것도 안 한다.
"""

import html
import logging
from datetime import datetime, timedelta

import config
import events
import schedule_plan
import telegram_client
from db import get_client

log = logging.getLogger("approval_digest")
STATE = {"approved": "✅ 승인됨", "pending": "⏳ 승인 대기", "edit_requested": "✏️ 수정 내용 기다리는 중",
         "regenerate": "🔁 다시 만드는 중"}


def build(now: datetime | None = None) -> tuple[str, list[dict]]:
    """알림 글과 버튼을 다시 붙일 승인 대기 카드 목록."""
    now = now or datetime.now(schedule_plan.KST)
    tomorrow = (now + timedelta(days=1)).date()
    cards = schedule_plan.open_cards(now)
    titles = {}
    if cards:
        rows = (get_client().table("cards").select("id,final_title").in_("id", [c["id"] for c in cards]).execute().data or [])
        titles = {r["id"]: r["final_title"] for r in rows}
    by_slot = {schedule_plan.parse_ts(c["scheduled_for"]).timestamp(): c for c in cards if c.get("scheduled_for")}
    reserves = [c for c in cards if c.get("reserve") and schedule_plan.fresh(c, now)]
    name = config.CHANNEL_CONFIG.get("name", config.CHANNEL)
    lines = [f"🕡 <b>{html.escape(name)}</b> 내일 {tomorrow:%m/%d}({schedule_plan.WEEKDAYS[tomorrow.weekday()]}) 게시 승인 확인"]
    waiting = []

    def line(prefix: str, card: dict | None) -> str:
        if not card:
            return f"{prefix} — ❌ 카드 없음"
        if card["approval"] in ("pending", "edit_requested"):
            waiting.append(card)
        return f"{prefix} — card {card['id']} {STATE.get(card['approval'], card['approval'])} · {html.escape((titles.get(card['id']) or '')[:40])}"

    for slot in schedule_plan.slot_times(tomorrow):
        lines.append(line(f"📅 {slot:%H:%M}", by_slot.get(slot.timestamp())))
    for card in reserves or [None]:
        lines.append(line("🧺 예비분", card))
    ready_reserve = any(c["approval"] == "approved" for c in reserves)
    lines.append("")
    if waiting:
        lines.append("아래 카드에서 [✅ 게시]·[✏️ 수정 요청]을 눌러줘.")
    lines.append("게시 시각까지 승인 안 된 자리는 " + ("승인된 예비분이 대신 올라가." if ready_reserve
                                              else "예비분도 승인돼 있어야 대신 올라가(지금은 승인된 예비분 없음)."))
    return "\n".join(lines), waiting


def main() -> None:
    if not schedule_plan.enabled():
        print(f"[approval_digest] {config.CHANNEL} 채널은 nextday가 꺼져 있어 건너뜀")
        return
    text, waiting = build()
    print(text)
    telegram_client.send_message(text)
    for card in waiting:
        telegram_client.send_message(f"⏳ 승인 대기 · card {card['id']}", reply_markup=telegram_client.card_keyboard(card["id"]))
    events.record("approval", "started", message=f"18:30 승인 모아보기 보냄(대기 {len(waiting)}건)")


if __name__ == "__main__":
    main()

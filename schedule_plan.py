"""전날 제작(Phase 5, 2026-11-14 대장 결정).

17:30 `run_daily.py nextday`가 다음 날 게시 시각(channel.json publish_times)마다 카드 1개 + 예비분(reserve_cards, 기본 1)을
만들어 승인 요청을 보내고, 18:30 `approval_digest.py`가 승인 대기 목록을 다시 알린다.
게시(10분 작업)는 게시 시각마다 1개만 올린다. 그 시각 카드가 승인 안 됐거나 반려면 → 예비분을 대신 올린다.

channel.json
- "nextday": true        켜기(채널마다. 디징 먼저 1주 시험)
- "publish_times"        게시 시각 목록(디징 12:00·19:00, 묘한경제 07:30)
- "reserve_cards"        예비분 목표 개수(기본 1)
- "reserve_max_days"     예비분·늦게 승인된 카드를 쓸 수 있는 기간(만든 날부터, 기본 3. 묘한경제 뉴스는 2)
cards 칸: scheduled_for(게시 예정 시각), reserve(예비분). sql/2026-11-14-nextday-schedule.sql
"""

from datetime import date, datetime, time, timedelta, timezone

import config
from db import get_client

KST = timezone(timedelta(hours=9))
OPEN_STATES = ("pending", "approved", "regenerate", "edit_requested")
WEEKDAYS = "월화수목금토일"


def enabled() -> bool:
    return bool(config.CHANNEL_CONFIG.get("nextday"))


def reserve_target() -> int:
    return int(config.CHANNEL_CONFIG.get("reserve_cards", 1))


def reserve_max_days() -> int:
    return int(config.CHANNEL_CONFIG.get("reserve_max_days", 3))


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=KST)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def slot_times(day: date) -> list[datetime]:
    out = []
    for text in config.CHANNEL_CONFIG.get("publish_times") or []:
        h, m = (int(x) for x in text.split(":"))
        out.append(datetime.combine(day, time(h, m), tzinfo=KST))
    return sorted(out)


def fresh(card: dict, now: datetime) -> bool:
    """예비분·늦게 승인된 카드는 만든 지 reserve_max_days 안의 것만 쓴다(묵은 뉴스 게시 방지)."""
    created = parse_ts(card.get("created_at"))
    return bool(created) and now - created <= timedelta(days=reserve_max_days())


def open_cards(now: datetime | None = None) -> list[dict]:
    """아직 안 올라갔고 승인 흐름에 있는 이 채널 카드(만든 지 reserve_max_days·3일 중 긴 기간 안)."""
    now = now or datetime.now(KST)
    since = now - timedelta(days=max(reserve_max_days(), 3))
    return (get_client().table("cards").select("id,category,approval,scheduled_for,reserve,created_at")
            .eq("published", False).in_("approval", list(OPEN_STATES)).in_("category", list(config.CATEGORIES))
            .gte("created_at", since.astimezone(timezone.utc).isoformat()).execute().data or [])


def plan_for(day: date, now: datetime | None = None, cards: list[dict] | None = None) -> list[dict]:
    """day에 필요한데 아직 없는 카드 자리: 게시 시각마다 {"scheduled_for": ...}, 모자란 예비분만큼 {"reserve": True}."""
    now = now or datetime.now(KST)
    cards = open_cards(now) if cards is None else cards
    taken = {parse_ts(c["scheduled_for"]).timestamp() for c in cards if c.get("scheduled_for")}
    # 오늘 자리를 채울 때(run_daily nextday today)는 승인받을 시간이 남은 시각(지금+1시간 뒤)만
    need = [{"scheduled_for": t.isoformat(), "reserve": False} for t in slot_times(day)
            if t.timestamp() not in taken and t > now + timedelta(hours=1)]
    have = sum(1 for c in cards if c.get("reserve") and fresh(c, now))
    need += [{"scheduled_for": None, "reserve": True}] * max(0, reserve_target() - have)
    return need


def label(schedule: dict | None) -> str:
    """미리보기·알림에 붙이는 한 줄."""
    if not schedule:
        return ""
    if schedule.get("reserve"):
        return f"🧺 예비분 · 게시 시각 카드가 승인 안 됐거나 반려면 대신 올라감({reserve_max_days()}일 안)"
    when = parse_ts(schedule.get("scheduled_for"))
    if not when:
        return ""
    when = when.astimezone(KST)
    return f"📅 {when:%m/%d}({WEEKDAYS[when.weekday()]}) {when:%H:%M} 게시 예정"


def current_slot(now: datetime) -> datetime | None:
    """지금 채울 게시 시각: 오늘 게시 시각 중 지금 이전의 가장 늦은 것."""
    past = [t for t in slot_times(now.astimezone(KST).date()) if t <= now]
    return past[-1] if past else None


def slot_filled(slot: datetime) -> bool:
    """그 게시 시각 이후 이미 게시했으면 True(시각마다 1개)."""
    rows = (get_client().table("pipeline_events").select("id")
            .eq("channel", config.CHANNEL).eq("stage", "publish").eq("status", "success")
            .gte("created_at", slot.astimezone(timezone.utc).isoformat()).limit(1).execute().data or [])
    return bool(rows)


def order_for_slot(items: list[dict], now: datetime, slot: datetime) -> list[dict]:
    """승인된 카드 중 이번 게시 시각에 올릴 순서: ① 이 시각 카드 ② 앞 시각에 못 올라간 카드·예정 시각 없는 카드
    ③ 예비분. ②③은 만든 지 reserve_max_days 안의 것만. 아직 시각이 안 된 카드는 절대 넣지 않는다."""
    exact, late, spare = [], [], []
    for item in items:
        when = parse_ts(item.get("scheduled_for"))
        if when and when > now:
            continue
        if when and when.timestamp() == slot.timestamp():
            exact.append(item)
        elif not fresh(item, now):
            continue
        elif item.get("reserve"):
            spare.append(item)
        else:
            late.append(item)
    key = lambda i: (parse_ts(i.get("scheduled_for")) or parse_ts(i.get("created_at")) or now)
    return exact + sorted(late, key=key) + sorted(spare, key=lambda i: parse_ts(i.get("created_at")) or now)


def choose(items: list[dict], now: datetime | None = None) -> tuple[list[dict], str]:
    """10분 게시 작업이 이번 실행에 올릴 후보(순서대로 시도, 실제로는 1개만 올림)와, 없으면 그 이유."""
    now = now or datetime.now(KST)
    slot = current_slot(now)
    if not slot:
        return [], "오늘 첫 게시 시각 전"
    if slot_filled(slot):
        return [], f"{slot:%H:%M} 게시 시각은 이미 올림"
    ordered = order_for_slot(items, now, slot)
    return ordered, "" if ordered else f"{slot:%H:%M}에 올릴 승인 카드·예비분 없음"


def not_due(item: dict, now: datetime | None = None) -> bool:
    """예정 시각이 아직 안 된 카드(어느 경로로도 미리 올라가지 않게)."""
    when = parse_ts(item.get("scheduled_for"))
    return bool(when) and when > (now or datetime.now(KST))

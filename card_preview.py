"""승인이 필요한 채널(디징 require_approval)의 카드 승인 요청(2026-11-02).

카드 생성 직후 텔레그램으로 카드 이미지 묶음(sendMediaGroup) + 캡션 전문 + 공식 공고 링크 + 버튼
[게시][다시 만들기][건너뛰기]를 보낸다. 버튼은 webhook/api/index.py가 cards.approval로 바꾸고,
게시는 10분 작업(publish_instagram.py --approved-only)이 한다. 대시보드 카드 탭에도 같은 버튼이 있다.
"""

import html
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
import events
import telegram_client
from db import get_client

log = logging.getLogger("card_preview")
KST = timezone(timedelta(hours=9))


def required() -> bool:
    return bool(config.CHANNEL_CONFIG.get("require_approval"))


def preview_text(card_id: int, caption: str, item: dict | None, deck: dict, note: str = "") -> str:
    item = item or {}
    lines = [f"🆕 승인 요청 · card {card_id}{f' · {note}' if note else ''}", f"<b>{html.escape(item.get('title') or '')}</b>"]
    if item.get("apply_end"):
        left = (date.fromisoformat(item["apply_end"]) - datetime.now(KST).date()).days
        lines.append(f"마감 {item['apply_end']} (오늘 기준 D-{left}, 게시 직전에 캡션 D-n을 다시 계산)")
    if item.get("official_url"):
        lines.append(f"공식 공고: {html.escape(item['official_url'])}")
    if item.get("url") and item.get("url") != item.get("official_url"):
        lines.append(f"수집처: {html.escape(item['url'])}")
    if deck.get("collaborators"):
        lines.append("공동 작업자: " + ", ".join("@" + html.escape(c) for c in deck["collaborators"]))
    lines += review_lines(deck)
    lines += ["", "<b>캡션</b>", html.escape(caption)]
    return "\n".join(lines)[:4000]


def review_lines(deck: dict) -> list[str]:
    """사실표 작성(v2) 덱이면 표지 후보 3개(고른 것 먼저)와 디자이너 검수 3개를 미리보기에 붙인다."""
    out = []
    pick = deck.get("cover_pick") or {}
    passing = [c["title"] for c in deck.get("cover_candidates") or [] if not c.get("problems")]
    if pick:
        others = [t for t in passing if t != pick.get("title")][:2]
        flat = lambda t: html.escape(str(t).replace("\n", " / "))
        out += ["", f"<b>표지</b> {flat(pick.get('title'))} — {html.escape(pick.get('reason') or '')}"]
        out += [f"· 다른 후보: {flat(t)}" for t in others]
    review = [r for r in deck.get("designer_review") or [] if r.get("after") or r.get("error")]
    if review:
        out += ["", f"<b>디자이너 검수</b> ({deck.get('designer_neurons', '?')} 뉴런)"]
        for r in review[:3]:
            if r.get("error"):
                out.append(f"· {html.escape(r['error'])}")
                continue
            mark = "반영" if r.get("applied") else "보류"
            out.append(f"· [{mark}] {r.get('slide')}장 {html.escape(str(r.get('field')))}: {html.escape(str(r.get("after")).replace(chr(10), " / "))[:80]}"
                       f" — {html.escape(str(r.get('reason') or ''))[:60]}")
    return out


def send_preview(card_id: int, out_dir: Path, news_item_id: int | None, note: str = "") -> bool:
    """미리보기를 보낸다. 실패해도 카드는 pending으로 남아 대시보드에서 승인할 수 있으므로 예외를 올리지 않는다."""
    try:
        pngs = sorted(Path(out_dir).glob("[0-9][0-9].png"))
        caption_path = Path(out_dir) / "caption.txt"
        caption = caption_path.read_text(encoding="utf-8") if caption_path.exists() else ""
        deck_path = Path(out_dir) / "deck.json"
        deck = json.loads(deck_path.read_text(encoding="utf-8")) if deck_path.exists() else {}
        item = None
        if news_item_id:
            rows = (get_client().table("news_items").select("title,url,official_url,apply_end")
                    .eq("id", news_item_id).execute().data or [])
            item = rows[0] if rows else None
        if pngs:
            telegram_client.send_media_group([str(p) for p in pngs])
        telegram_client.send_message(preview_text(card_id, caption, item, deck, note),
                                     reply_markup=telegram_client.card_keyboard(card_id))
        events.record("approval", "started", card_id=card_id, news_item_id=news_item_id,
                      message=f"승인 요청 보냄(이미지 {len(pngs)}장){f' · {note}' if note else ''}")
        return True
    except Exception as e:
        log.error("card %s 미리보기 전송 실패: %s", card_id, e)
        events.record("approval", "failed", card_id=card_id, news_item_id=news_item_id, message=f"미리보기 전송 실패: {e}")
        return False

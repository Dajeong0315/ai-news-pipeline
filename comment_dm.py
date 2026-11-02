"""댓글 → 자료 DM 준비(2026-11-02). DM 발송은 리틀리가 하고, 이 코드는 캡션 안내 문구와 자료 목록만 만든다.

- 캡션: channel.json comment_dm의 caption_line을 캡션 본문 마지막 친구 태그 줄(디징) 바로 앞에 한 줄 넣는다.
  친구 태그 줄이 없는 채널(경제)은 본문 마지막 줄("참고: …") 바로 앞. comment_dm.enabled가 false이거나 hub_url이 비어 있으면
  넣지 않는다(묘한경제는 리틀리 연동 전이라 꺼 둠). 카드 생성 때 넣고, 못 넣었으면 게시 직전에 넣는다.
- 자료 목록: 게시 성공 시 channels/<채널>/outbox/guides.json에 항목을 넣는다(꺼 둔 채널은 안 넣음). Aside가 노션 하위 페이지·
  리틀리 설정을 하고 notion_url·notion_page_id·littly_set·guide_done_at을 채운다. 형식은 channels/youth/outbox/README.md.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

log = logging.getLogger("comment_dm")
KST = timezone(timedelta(hours=9))
CFG = config.CHANNEL_CONFIG.get("comment_dm") or {}
GUIDES_PATH = config.CHANNEL_DIR / "outbox" / "guides.json"
CLOSING_WORDS = tuple(config.CHANNEL_CONFIG.get("caption_closing_words", ()))
CAPTION_HARD_MAX = 2200
_MATERIAL_RE = re.compile(r"(제출\s*서류|구비\s*서류|준비물|제출물|필요\s*서류)")


def enabled() -> bool:
    return bool(CFG) and CFG.get("enabled") is not False


def caption_line() -> str | None:
    if not enabled():
        return None
    line = str(CFG.get("caption_line") or "").strip()
    return line if line and str(CFG.get("hub_url") or "").strip() else None


def add_line(caption: str) -> str:
    """안내 문구를 넣은 캡션. 넣을 문구가 없거나 이미 있으면 그대로."""
    line = caption_line()
    if not line or line in caption:
        return caption
    lines = caption.split("\n")
    body_end = next((i for i, x in enumerate(lines) if not x.strip()), len(lines))  # 첫 빈 줄 = 고지·해시태그 시작
    if body_end == 0:
        return caption
    at = body_end - 1  # 기본: 본문 마지막 줄 앞
    if CLOSING_WORDS:
        tagged = [i for i in range(body_end) if any(w in lines[i] for w in CLOSING_WORDS)]
        if tagged:
            at = tagged[-1]
    return "\n".join(lines[:at] + [line] + lines[at:])


def _caption_value(caption: str, mark: str) -> str | None:
    for line in caption.splitlines():
        if line.startswith(mark):
            return re.sub(r"^[^:：]*[:：]\s*", "", line[len(mark):].strip()) or None
    return None


def _materials(raw: str) -> str | None:
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    for i, x in enumerate(lines):
        if _MATERIAL_RE.search(x):
            return " / ".join(lines[i:i + 3])[:400]
    return None


def _with_time(day: str | None, raw: str) -> str | None:
    """공고 날짜(YYYY-MM-DD)에 원문 속 시각을 붙인다. 원문에서 그 월.일 바로 뒤 HH:MM을 찾으면
    2026-11-09T18:00:00+09:00, 못 찾으면 날짜만(시각을 지어내지 않는다)."""
    if not day:
        return None
    d = datetime.fromisoformat(day).date()
    m = re.search(rf"(?<!\d)0?{d.month}\s*[./]\s*0?{d.day}(?!\d)[^\n\d]{{0,12}}(\d{{1,2}})\s*:\s*(\d{{2}})", raw)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return d.isoformat()
    return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)), tzinfo=KST).isoformat()


def guide_entry(media_id: str, permalink: str | None, title: str, caption: str, item: dict | None,
                guide_notes: list[str] | None = None) -> dict:
    item = item or {}
    info = item.get("listing") or {}
    raw = info.get("raw_text") or ""
    return {
        "media_id": str(media_id),
        "permalink": permalink,
        "title": title,
        "host": item.get("source") or info.get("host"),
        "target": info.get("target") or _caption_value(caption, "📌"),
        "benefit": info.get("benefit") or _caption_value(caption, "💼"),
        "apply_start": _with_time(item.get("apply_start"), raw),
        "apply_end": _with_time(item.get("apply_end"), raw),
        "how_to_apply": _caption_value(caption, "✅"),
        "documents": _materials(raw),
        "cautions": _caption_value(caption, "⚠️"),
        "official_url": item.get("official_url") or item.get("url"),
        "caption": caption,
        "guide_notes": guide_notes or [],  # 카드·캡션에 못 넣고 가이드로 보낸 사실(v2 deck.guide_notes)
        "published_at": datetime.now(KST).isoformat(timespec="seconds"),
        "notion_url": None,
        "notion_page_id": None,
        "littly_set": None,
        "guide_done_at": None,
    }


def add_guide(entry: dict, path: Path = GUIDES_PATH) -> None:
    """guides.json에 항목을 넣는다. 실패해도 게시는 끝났으므로 로그만 남긴다."""
    if not enabled():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
        if any(str(i.get("media_id")) == entry["media_id"] for i in data["items"]):
            return
        data["items"].append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        log.info("자료 목록(guides.json)에 추가: %s", entry["media_id"])
    except Exception as e:
        log.error("guides.json 기록 실패: %s", e)

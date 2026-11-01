"""게시한 캐러셀을 Aside 루틴이 스토리로 "다시 공유"하도록 넘기는 목록. 채널 설정 story_queue(디징).

파이프라인의 헤드리스 브라우저(share_to_story.py)는 Meta가 로그인을 풀어 버려(2026-11-01), 공유는 운영자가 평소 쓰는
Aside 브라우저 루틴(12:30·19:30)이 맡는다. 파이프라인은 게시 직후 channels/<채널>/outbox/stories.json에 게시물을 적고,
Aside는 shared_at이 비어 있는 항목만 공유한 뒤 shared_at을 채운다(형식은 outbox/README.md).

다음 게시 실행 때 check()가 Graph API /{ig-id}/stories로 실제 스토리를 확인해 story_id를 적고,
공유가 안 됐거나 공유했다는데 스토리가 안 보이면 텔레그램으로 한 번 알린다(같은 항목은 다시 알리지 않음).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import config
import events
import telegram_client

log = logging.getLogger("story_queue")

KST = timezone(timedelta(hours=9))
QUEUE_PATH = config.CHANNEL_DIR / "outbox" / "stories.json"
KEEP_DAYS = 14                          # 이보다 오래된 항목은 파일에서 지운다
SHARE_DEADLINE = timedelta(hours=2)     # 게시 뒤 이 시간이 지나도 shared_at이 비면 알림
VERIFY_GRACE = timedelta(minutes=30)    # 공유 뒤 이 시간이 지나도 스토리가 안 보이면 알림
MATCH_WINDOW = (timedelta(minutes=-15), timedelta(minutes=30))  # shared_at 기준 스토리 게시 시각 허용 범위
STORY_LIFETIME = timedelta(hours=24)    # 스토리는 24시간 뒤 /stories에서 사라져 확인할 수 없다


def enabled() -> bool:
    return bool(config.CHANNEL_CONFIG.get("story_queue"))


def _load(path: Path = QUEUE_PATH) -> dict:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(data: dict, path: Path = QUEUE_PATH) -> None:
    cutoff = datetime.now(KST) - timedelta(days=KEEP_DAYS)
    data["items"] = [i for i in data.get("items", []) if _when(i.get("published_at")) and _when(i["published_at"]) >= cutoff]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)  # 시간대 없이 적었으면 KST로 본다


def _graph(path: str, **params) -> dict:
    resp = httpx.get(f"https://graph.facebook.com/{config.INSTAGRAM_API_VERSION}/{path}",
                     params={**params, "access_token": config.INSTAGRAM_ACCESS_TOKEN}, timeout=30)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Graph API 오류: {data['error'].get('message')}")
    return data


def add(media_id: str, title: str, card_id: int | None) -> None:
    """방금 게시한 캐러셀을 공유 대기 목록에 넣는다. 실패해도 게시는 이미 끝났으므로 로그만 남긴다."""
    try:
        data = _load()
        if any(str(i.get("media_id")) == str(media_id) for i in data["items"]):
            return
        try:  # 운영자가 손으로 공유할 때 쓰는 링크. Aside는 media_id로 대조하므로 없어도 된다
            permalink = _graph(str(media_id), fields="permalink").get("permalink")
        except Exception:
            permalink = None
        data["items"].append({
            "media_id": str(media_id), "permalink": permalink, "title": title, "card_id": card_id,
            "published_at": datetime.now(KST).isoformat(timespec="seconds"),
            "shared_at": None, "shared_by": None, "notes": None,
            "story_id": None, "verified_at": None, "alerted": None,
        })
        _save(data)
        log.info("스토리 공유 대기 목록에 추가: %s", media_id)
    except Exception as e:
        log.error("스토리 공유 대기 목록 기록 실패: %s", e)


def _stories() -> list[dict]:
    return _graph(f"{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/stories", fields="id,timestamp,permalink").get("data", [])


def check(now: datetime | None = None, stories: list[dict] | None = None) -> None:
    """공유 결과를 Graph API로 확인하고 빠진 것을 알린다. 게시 실행마다 한 번. 어떤 실패도 예외로 올리지 않는다."""
    try:
        data = _load()
    except Exception as e:
        log.error("스토리 공유 목록 읽기 실패: %s", e)
        return
    now = now or datetime.now(KST)
    used = {i["story_id"] for i in data["items"] if i.get("story_id")}
    alerts, changed = [], False
    for item in data["items"]:
        late_share = item.get("alerted") == "not_shared" and item.get("shared_at")  # 알림 뒤 늦게라도 공유했으면 확인
        if item.get("story_id") or (item.get("alerted") and not late_share):
            continue
        published, shared = _when(item.get("published_at")), _when(item.get("shared_at"))
        if not shared:
            if published and now - published >= SHARE_DEADLINE:
                item["alerted"] = "not_shared"
                alerts.append(f"스토리 공유 안 됨(게시 {published:%m/%d %H:%M}): {item.get('title')}\n{item.get('permalink')}")
            continue
        if now - shared > STORY_LIFETIME:  # 확인 기회를 놓침(스토리가 이미 내려감)
            item["alerted"] = "unverifiable"
            changed = True
            continue
        if stories is None:
            try:
                stories = _stories()
            except Exception as e:
                log.warning("스토리 조회 실패, 다음 실행에서 다시 확인: %s", e)
                break
        lo, hi = shared + MATCH_WINDOW[0], shared + MATCH_WINDOW[1]
        match = next((s for s in sorted(stories, key=lambda s: s["timestamp"])
                      if s["id"] not in used and lo <= _when(s["timestamp"].replace("+0000", "+00:00")) <= hi), None)
        if match:
            used.add(match["id"])
            item.update(story_id=match["id"], verified_at=now.isoformat(timespec="seconds"))
            events.record("story", "success", card_id=item.get("card_id"), message="Aside 루틴 스토리 공유 확인",
                          detail={"media_id": item["media_id"], "story": match, "shared_by": item.get("shared_by")})
        elif now - shared >= VERIFY_GRACE:
            item["alerted"] = "not_found"
            alerts.append(f"스토리 공유했다는데 /stories에 안 보임(공유 {shared:%m/%d %H:%M}): {item.get('title')}\n{item.get('permalink')}")
        else:
            continue
        changed = True
    for text in alerts:
        events.record("story", "failed", message=text.splitlines()[0])
        try:
            telegram_client.send_message(f"⚠️ {text}")
        except Exception as e:
            log.warning("텔레그램 알림 실패: %s", e)
    if alerts or changed:
        try:
            _save(data)
        except Exception as e:
            log.error("스토리 공유 목록 저장 실패: %s", e)

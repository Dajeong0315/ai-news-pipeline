"""방금 올린 인스타 게시물을 스토리로 "다시 공유"(게시물 스티커)한다. 디징 채널(share_post_to_story).

Graph API에는 게시물을 스토리로 공유하는 기능이 없어, Meta Business Suite 웹 화면을 Playwright(fb_story_share.js)로
조작한다(2026-11-01 Aside가 수동으로 확인한 경로). 로그인은 저장소 밖 전용 브라우저 프로필(%LOCALAPPDATA%\\cardnews-fb-profile)에
남기고, 최초 1회만 운영자가 열린 창에서 직접 페이스북에 로그인한다(코드는 비밀번호를 다루지 않는다).

실패(로그인 풀림·2단계 인증·화면 변경)해도 캐러셀 게시는 이미 성공한 것이므로 예외를 던지지 않고,
스크린샷을 logs/story/에 남기고 텔레그램으로 알린다. 공유 뒤에는 Graph API /{ig-id}/stories로 새 스토리를 확인한다.

실행:
    python share_to_story.py --login                       # 최초 1회(창이 열림, 운영자가 로그인)
    python share_to_story.py <media_id|permalink> --dry-run   # "스토리에 추가" 직전 대화상자 스크린샷만
    python share_to_story.py <media_id|permalink>             # 실제 공유
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import config
import events
import telegram_client

log = logging.getLogger("share_to_story")

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "cardnews-fb-profile"
SHOT_DIR = ROOT / "logs" / "story"
GRAPH = f"https://graph.facebook.com/{config.INSTAGRAM_API_VERSION}"
STORY_CHECK_TRIES, STORY_CHECK_WAIT = 6, 10  # 공유 뒤 스토리가 Graph API에 보일 때까지 최대 1분


def _graph(path: str, **params) -> dict:
    resp = httpx.get(f"{GRAPH}/{path}", params={**params, "access_token": config.INSTAGRAM_ACCESS_TOKEN}, timeout=30)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Graph API 오류: {data['error'].get('message')}")
    return data


def resolve_media(ref: str) -> dict:
    """media_id·permalink·게시 시각(ISO 앞부분, 예 2026-11-01T04:30) 중 하나로 이 계정의 게시물을 찾는다."""
    fields = "id,permalink,shortcode,timestamp,media_type,caption"
    if ref.isdigit():
        return _graph(ref, fields=fields)
    media = _graph(f"{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/media", fields=fields, limit=30).get("data", [])
    key = ref.rstrip("/")
    found = [m for m in media if m.get("permalink", "").rstrip("/") == key or m.get("timestamp", "").startswith(ref)]
    if len(found) != 1:
        raise ValueError(f"게시물을 하나로 특정하지 못함({len(found)}건): {ref}")
    return found[0]


def run_browser(mode: str, media: dict | None = None, dry_run: bool = False, headful: bool = False) -> dict:
    page_id = config.CHANNEL_CONFIG.get("fb_page_id")
    if not page_id:
        raise RuntimeError("채널 설정 fb_page_id(Business Suite asset_id)가 없음")
    job = {
        "mode": mode, "asset_id": page_id, "profile_dir": str(PROFILE_DIR), "shot_dir": str(SHOT_DIR),
        "media_id": (media or {}).get("id"), "shortcode": (media or {}).get("shortcode"),
        "dry_run": dry_run, "headful": headful,
    }
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    job_path = SHOT_DIR / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    timeout = 15 * 60 if mode == "login" else 6 * 60
    result = subprocess.run(["node", str(ROOT / "fb_story_share.js"), str(job_path)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if not lines:
        return {"ok": False, "step": "node", "message": (result.stderr or result.stdout).strip()[-500:]}
    return json.loads(lines[-1])


def new_story_after(started: datetime) -> dict | None:
    for _ in range(STORY_CHECK_TRIES):
        stories = _graph(f"{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/stories", fields="id,media_type,permalink,timestamp").get("data", [])
        fresh = [s for s in stories if datetime.strptime(s["timestamp"], "%Y-%m-%dT%H:%M:%S%z") >= started]
        if fresh:
            return fresh[0]
        time.sleep(STORY_CHECK_WAIT)
    return None


def share(media_ref: str, dry_run: bool = False, headful: bool = False, card_id: int | None = None,
          category: str | None = None) -> bool:
    """게시물을 스토리로 공유. 성공(dry_run이면 대화상자까지 도달)하면 True. 어떤 실패도 예외로 올리지 않는다."""
    started = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        media = resolve_media(media_ref)
        result = run_browser("share", media, dry_run=dry_run, headful=headful)
    except Exception as e:  # Graph API·node 실행 자체 실패
        media, result = None, {"ok": False, "step": "setup", "message": str(e)[:500]}
    detail = {"media_id": (media or {}).get("id"), "permalink": (media or {}).get("permalink"), "dry_run": dry_run,
              "step": result.get("step"), "screenshot": result.get("screenshot"), "copied_ids": result.get("copied_ids")}
    if not result.get("ok"):
        log.error("스토리 공유 실패(%s): %s", result.get("step"), result.get("message"))
        events.record("story", "failed", category=category, card_id=card_id, message=result.get("message"), detail=detail)
        if dry_run:  # 시험 실행 실패는 운영자가 화면 앞에 있으므로 알림을 보내지 않는다
            return False
        try:
            telegram_client.send_message(f"⚠️ 스토리 공유 실패(캐러셀은 게시됨)\n{(media or {}).get('permalink') or media_ref}\n"
                                         f"{result.get('step')}: {result.get('message')}\n스크린샷: {result.get('screenshot')}")
        except Exception as e:
            log.warning("텔레그램 알림 실패: %s", e)
        return False
    if dry_run:
        log.info("스토리 공유 dry-run: %s · 스크린샷 %s", result.get("message"), result.get("screenshot"))
        events.record("story", "skipped", category=category, card_id=card_id, message="dry-run: 스토리에 추가 직전까지", detail=detail)
        return True
    story = None
    try:
        story = new_story_after(started)
    except Exception as e:
        log.warning("스토리 확인 조회 실패: %s", e)
    detail["story"] = story
    if story:
        log.info("스토리 공유 완료: story %s", story.get("id"))
        events.record("story", "success", category=category, card_id=card_id, message="스토리에 다시 공유 완료", detail=detail)
    else:  # 버튼은 눌렀지만 API로 아직 안 보임 → 사람이 확인하도록 알린다
        log.warning("스토리 공유 버튼은 눌렀지만 Graph API에서 새 스토리를 못 찾음")
        events.record("story", "failed", category=category, card_id=card_id,
                      message="스토리에 추가를 눌렀지만 /stories에서 새 스토리가 안 보임", detail=detail)
        try:
            telegram_client.send_message(f"⚠️ 스토리 공유 확인 필요: 버튼은 눌렀지만 새 스토리가 API에 안 보임\n{detail['permalink']}")
        except Exception as e:
            log.warning("텔레그램 알림 실패: %s", e)
    return bool(story)


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if "--login" in argv:
        result = run_browser("login", headful=True)
        print(json.dumps(result, ensure_ascii=False))
        return
    refs = [a for a in argv if not a.startswith("--")]
    if len(refs) != 1:
        print(__doc__)
        sys.exit(1)
    ok = share(refs[0], dry_run="--dry-run" in argv, headful="--headful" in argv)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1:])

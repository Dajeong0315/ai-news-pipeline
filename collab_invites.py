"""공동 작업(콜라보) 초대 자동 수락(2026-11-02). 우리 계정끼리만 받는다.

한 채널이 collaborators로 다른 우리 채널을 넣어 게시하면, 초대받은 채널 토큰으로
GET /{ig-user-id}/collaboration_invites 를 보고, 보낸 계정이 화이트리스트(channels/*/channel.json의 ig_username)에 있는
초대만 POST /{ig-user-id}/collaboration_invites (media_id, accept=true)로 수락한다. 그 밖의 초대는 건드리지 않는다.

게시 직후 publish_instagram이 초대받은 채널 환경으로 이 스크립트를 띄운다(토큰이 채널마다 달라서 별도 프로세스).

실행:
    CHANNEL=economy python collab_invites.py --dry-run          # 받은 초대만 출력
    CHANNEL=economy python collab_invites.py --media 1789... --wait 120   # 그 게시물 초대가 올 때까지 기다렸다 수락
"""

import json
import logging
import sys
import time
from pathlib import Path

import httpx

import config
import events

log = logging.getLogger("collab_invites")
GRAPH = f"https://graph.facebook.com/{config.INSTAGRAM_API_VERSION}"
POLL_SEC = 15


def our_usernames() -> set[str]:
    """화이트리스트: 이 저장소가 운영하는 채널 계정(자기 자신 제외)."""
    names = set()
    for path in (Path(__file__).resolve().parent / "channels").glob("*/channel.json"):
        name = json.loads(path.read_text(encoding="utf-8")).get("ig_username")
        if name:
            names.add(name.lower())
    return names - {str(config.CHANNEL_CONFIG.get("ig_username") or "").lower()}


def _call(method: str, **params) -> dict:
    url = f"{GRAPH}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/collaboration_invites"
    headers = {"Authorization": f"Bearer {config.INSTAGRAM_ACCESS_TOKEN}"}
    resp = httpx.request(method, url, params=params, headers=headers, timeout=30)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Graph API 오류: {data['error'].get('message')}")
    return data


def pending_invites() -> list[dict]:
    return _call("GET", fields="media_id,media_owner_username,caption,media_url").get("data", [])


def accept_from_ours(media_id: str | None = None, dry_run: bool = False) -> list[str]:
    """화이트리스트 계정이 보낸 초대를 수락하고 수락한 media_id 목록을 돌려준다. media_id를 주면 그 게시물만."""
    allowed = our_usernames()
    accepted = []
    for invite in pending_invites():
        owner = str(invite.get("media_owner_username") or "").lower()
        mid = str(invite.get("media_id") or "")
        if media_id and mid != str(media_id):
            continue
        if owner not in allowed:
            log.info("화이트리스트 밖 초대는 그대로 둠: @%s %s", owner, mid)
            continue
        if dry_run:
            log.info("[dry-run] 수락할 초대: @%s %s", owner, mid)
            accepted.append(mid)
            continue
        _call("POST", media_id=mid, accept="true")
        log.info("공동 작업 초대 수락: @%s %s", owner, mid)
        events.record("collab", "success", message=f"@{owner} 공동 작업 초대 수락", detail={"media_id": mid, "owner": owner})
        accepted.append(mid)
    return accepted


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    media_id = argv[argv.index("--media") + 1] if "--media" in argv else None
    wait = int(argv[argv.index("--wait") + 1]) if "--wait" in argv else 0
    dry_run = "--dry-run" in argv
    deadline = time.time() + wait
    while True:
        try:
            accepted = accept_from_ours(media_id, dry_run)
        except Exception as e:
            log.error("초대 확인 실패: %s", e)
            events.record("collab", "failed", message=f"초대 확인 실패: {e}", detail={"media_id": media_id})
            return 1
        if accepted or not media_id or time.time() >= deadline:
            break
        time.sleep(POLL_SEC)
    if media_id and not accepted:
        log.warning("%s 게시물 초대가 %d초 안에 안 보임", media_id, wait)
        if not dry_run:
            events.record("collab", "failed", message=f"공동 작업 초대가 {wait}초 안에 안 보임(수동 수락 필요)", detail={"media_id": media_id})
        return 1
    print(json.dumps({"channel": config.CHANNEL, "accepted": accepted, "dry_run": dry_run}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

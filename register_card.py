"""output 폴더에 직접 넣은 카드(Aside가 만든 콜라보 카드 등)를 승인 흐름에 올린다(2026-11-02).

폴더에 01.png~NN.png, caption.txt, deck.json이 있어야 한다. deck.json의 "collaborators"(인스타 사용자명, 최대 3)가
있으면 게시할 때 공동 작업자로 들어가고, 그게 우리 채널(@myohan.economy 등)이면 그 채널 토큰으로 초대를 자동 수락한다.
cards 행을 approval='pending'으로 넣고 텔레그램 승인 요청을 보낸다. 게시는 [게시] 승인 뒤 10분 작업이 한다.
공고 원본이 있으면 --news-item으로 연결한다(마감 확인·D-n 갱신·공식 링크에 쓰임).

실행:
    CHANNEL=youth python register_card.py output/2026-11-03/collab-myohan --category youth_support [--news-item 1234] [--dry-run]
"""

import json
import sys
from pathlib import Path

import card_preview
import config
from db import get_client
from instagram_client import upload_to_storage


def check_folder(folder: Path) -> tuple[list[Path], dict]:
    pngs = sorted(folder.glob("[0-9][0-9].png"))
    if not pngs:
        raise SystemExit(f"카드 이미지(01.png~)가 없음: {folder}")
    if len(pngs) > 10:
        raise SystemExit(f"캐러셀은 최대 10장(지금 {len(pngs)}장)")
    caption = folder / "caption.txt"
    if not caption.exists() or not caption.read_text(encoding="utf-8").strip():
        raise SystemExit("caption.txt가 없거나 비었음")
    if len(caption.read_text(encoding="utf-8")) > 2200:
        raise SystemExit("caption.txt가 2200자를 넘음")
    deck_path = folder / "deck.json"
    deck = json.loads(deck_path.read_text(encoding="utf-8")) if deck_path.exists() else {}
    collaborators = deck.get("collaborators") or []
    if len(collaborators) > 3:
        raise SystemExit("collaborators는 최대 3명")
    return pngs, deck


def main(argv: list[str]) -> None:
    if not argv or argv[0].startswith("--"):
        print(__doc__)
        sys.exit(1)
    folder = Path(argv[0])
    category = argv[argv.index("--category") + 1] if "--category" in argv else None
    news_item_id = int(argv[argv.index("--news-item") + 1]) if "--news-item" in argv else None
    if category not in config.CATEGORIES:
        raise SystemExit(f"--category는 {list(config.CATEGORIES)} 중 하나")
    if not card_preview.required():
        raise SystemExit(f"{config.CHANNEL} 채널은 승인 흐름(require_approval)이 아님")
    pngs, deck = check_folder(folder)
    title = deck.get("title") or (folder / "caption.txt").read_text(encoding="utf-8").splitlines()[0]
    print(f"카드 {len(pngs)}장 · 공동 작업자 {deck.get('collaborators') or '없음'} · {title}")
    if "--dry-run" in argv:
        return
    client = get_client()
    order = (client.table("cards").select("id", count="exact").execute().count or 0) + 1
    card = client.table("cards").insert({
        "news_item_id": news_item_id, "category": category, "final_title": title[: config.TITLE_MAX_CHARS],
        "image_path": str(folder), "publish_order": order, "published": False, "approval": "pending",
    }).execute().data[0]
    for png in pngs:  # 대시보드 카드 탭 미리보기
        upload_to_storage(str(png))
    card_preview.send_preview(card["id"], folder, news_item_id, note="직접 넣은 카드")
    print(f"card {card['id']} 승인 요청 보냄")


if __name__ == "__main__":
    main(sys.argv[1:])

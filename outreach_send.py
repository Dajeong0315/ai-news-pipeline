"""영업팀 소통 목록 전송·장부 기록(2026-11-14).

영업팀(claude_tasks/outreach.md)이 `AI_OFFICE/outreach/<날짜>.json`을 쓰면 이 스크립트가
① 채널마다 텔레그램 한 통(게시물 링크 + 탭하면 복사되는 댓글) ② `<날짜>.md` 표 ③ ledger.json 추가를 한다.
버튼(팔로우·좋아요·댓글·서이추)은 대장님이 직접 누른다. 이 스크립트도 영업팀도 누르지 않는다.

사용: python outreach_send.py <날짜 YYYY-MM-DD> [--dry-run]
       python outreach_send.py --text <UTF-8 파일>     (주간 결과 보고 등 일반 글 보내기)

<날짜>.json 형식:
{"date": "2026-11-15",
 "channels": {"youth": [{"handle": "abc", "profile_url": "...", "post_url": "...", "reason": "...",
                         "actions": "팔로우 + 좋아요 + 댓글", "comment": "..."}],
              "economy": [...],
              "blog": [{"handle": "블로그 이름", "profile_url": "https://blog.naver.com/id", "post_url": "...",
                        "reason": "...", "actions": "서이추 + 공감 + 댓글", "comment": "...", "neighbor_msg": "..."}]},
 "skipped": {"economy": "로그인 화면이라 건너뜀"}}
"""

import html
import json
import sys
from datetime import datetime
from pathlib import Path

import phone_command

OUT_DIR = Path(r"H:\내 드라이브\컨텐츠 자동화\AI_OFFICE\outreach")
LEDGER = OUT_DIR / "ledger.json"
NAMES = {"youth": "디징 @dijing.kr", "economy": "묘한경제 @myohan.economy", "blog": "블로그 취향수집노트"}
REQUIRED = ("handle", "post_url", "reason", "comment")


def send_html(text: str) -> None:
    cfg = phone_command.load_config()
    if not cfg.get("token"):
        print("텔레그램 봇 설정 없음, 전송 생략")
        return
    phone_command.api(cfg, "sendMessage", {"chat_id": cfg["chat_id"], "text": text[:4000], "parse_mode": "HTML",
                                           "disable_web_page_preview": True})


def message(channel: str, items: list[dict], skipped: str | None) -> str:
    e = html.escape
    lines = [f"<b>[영업팀] {e(NAMES.get(channel, channel))} 오늘의 소통 {len(items)}개</b>"]
    if skipped:
        lines.append(f"건너뜀: {e(skipped)}")
    for n, it in enumerate(items, 1):
        lines += ["", f"{n}) <b>{e(it['handle'])}</b> · {e(it['reason'])}",
                  f'<a href="{e(it["post_url"], quote=True)}">게시물 열기</a>'
                  + (f' · <a href="{e(it["profile_url"], quote=True)}">프로필</a>' if it.get("profile_url") else ""),
                  f"할 일: {e(it.get('actions') or ('서이추 + 공감 + 댓글' if channel == 'blog' else '팔로우 + 좋아요 + 댓글'))}",
                  f"댓글: <code>{e(it['comment'])}</code>"]
        if it.get("neighbor_msg"):
            lines.append(f"서이추 메시지: <code>{e(it['neighbor_msg'])}</code>")
    lines += ["", "댓글·메시지는 탭하면 복사돼요. 마음에 안 드는 줄은 건너뛰어도 돼요."]
    return "\n".join(lines)


def table(data: dict) -> str:
    out = [f"# 영업팀 소통 목록 {data['date']}", ""]
    for ch, items in data["channels"].items():
        out += [f"## {NAMES.get(ch, ch)}", "", "| # | 계정·블로그 | 게시물 | 고른 이유 | 할 일 | 댓글 초안 | 서이추 메시지 |", "|-|-|-|-|-|-|-|"]
        clean = lambda s: str(s or "").replace("|", "/").replace("\n", " ")
        for n, it in enumerate(items, 1):
            out.append(f"| {n} | {clean(it['handle'])} | {clean(it['post_url'])} | {clean(it['reason'])} | {clean(it.get('actions'))} "
                       f"| {clean(it['comment'])} | {clean(it.get('neighbor_msg'))} |")
        if (data.get("skipped") or {}).get(ch):
            out.append(f"\n건너뜀: {data['skipped'][ch]}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--text"]:
        send_html(html.escape(Path(args[1]).read_text(encoding="utf-8").strip()))
        print("보냄")
        return 0
    if not args:
        print(__doc__)
        return 2
    day, dry = args[0], "--dry-run" in args
    data = json.loads((OUT_DIR / f"{day}.json").read_text(encoding="utf-8"))
    problems = [f"{ch} {n}: {k} 없음" for ch, items in data["channels"].items()
                for n, it in enumerate(items, 1) for k in REQUIRED if not it.get(k)]
    if problems:
        print("형식 오류(보내지 않음):", "; ".join(problems))
        return 1
    (OUT_DIR / f"{day}.md").write_text(table(data), encoding="utf-8")
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []
    seen = {(r["channel"], r["handle"], r["date"]) for r in ledger}
    for ch, items in data["channels"].items():
        for it in items:
            if (ch, it["handle"], day) not in seen:
                ledger.append({"date": day, "channel": ch, "handle": it["handle"], "profile_url": it.get("profile_url"),
                               "post_url": it["post_url"], "reason": it["reason"], "kind": it.get("kind"),
                               "followed_by_us": None, "follows_back": None, "checked_at": None})
    skipped = data.get("skipped") or {}
    for ch in list(data["channels"]) + [c for c in skipped if c not in data["channels"]]:
        items = data["channels"].get(ch, [])
        text = message(ch, items, skipped.get(ch))
        if dry:
            print(text, "\n-----")
        elif items or skipped.get(ch):
            send_html(text)
    if not dry:
        LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        json.loads(LEDGER.read_text(encoding="utf-8"))
    print(f"{'(dry-run) ' if dry else ''}채널 {len(data['channels'])}개, 장부 {len(ledger)}줄 · {datetime.now():%H:%M}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

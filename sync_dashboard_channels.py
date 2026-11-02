"""channels/<채널>/channel.json → webhook/api/channels.json (대시보드용 채널 설정 사본, 2026-11-02).

Vercel에는 webhook/ 폴더만 배포돼 channel.json을 직접 못 읽는다. 하루 게시 수·카테고리를 바꾸면 이걸 실행해
사본을 맞추고 같이 커밋한다(git push로 Vercel 자동 배포).

실행:
    python sync_dashboard_channels.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "webhook" / "api" / "channels.json"
ORDER = ("economy", "youth")  # 대시보드 채널 전환 순서(첫 번째가 기본)


def main() -> None:
    channels = {}
    for key in ORDER:
        cfg = json.loads((ROOT / "channels" / key / "channel.json").read_text(encoding="utf-8"))
        channels[key] = {
            "name": cfg["name"],
            "handle": f"@{cfg['ig_username']}",
            "daily_posts": cfg.get("daily_posts"),
            "require_approval": bool(cfg.get("require_approval")),
            "max_regenerations": cfg.get("max_regenerations"),
            "categories": cfg["categories"],
            # 지난 기록용 카테고리(경제 stock, 10/31 수집 종료). 기록 조회·이름 표시에만 쓰고 오늘 현황 칸에는 안 나온다.
            "retired_categories": cfg.get("retired_categories", {}),
        }
    OUT.write_text(json.dumps(channels, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for key, ch in channels.items():
        print(f"{key:8} 하루 {ch['daily_posts']}건  분야 {', '.join(ch['categories'].values())}")


if __name__ == "__main__":
    main()

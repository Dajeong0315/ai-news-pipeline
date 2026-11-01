"""채널 설정(channel.json publish_times)대로 Windows 작업 스케줄러 시각을 맞춘다(2026-10-31).

- 경제: EconCardNews-Collect(run_collect.bat = auto) 하루 1회, publish_times[0](기본 07:30).
  EconCardNews-Publish(18:00 publish)는 쓰지 않는다(비활성 그대로).
- 디징: DijingCardNews-1(run_youth.bat = auto, 선정·카드 2건 + 1건 게시), DijingCardNews-2(run_youth_publish.bat = 나머지 1건 게시).
  두 시각 간격은 min_post_gap_hours(6) 이상이어야 한다. 게시 단계(publish_instagram.posting_slots)도 같은 기준으로 한 번 더 막는다.

작업은 항상 "사용 안 함"으로 등록·수정한다. 켜는 건 운영자가 카드·캡션을 확인한 뒤 --enable로 따로 한다.

실행:
    python register_schedule.py            # 시각만 맞춤(사용 안 함 유지)
    python register_schedule.py --enable economy   # 운영자 승인 후에만
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = {  # 채널 → [(작업 이름, 배치 파일)], publish_times 순서대로
    "economy": [("EconCardNews-Collect", "run_collect.bat")],
    "youth": [("DijingCardNews-1", "run_youth.bat"), ("DijingCardNews-2", "run_youth_publish.bat")],
}


def ps(command: str) -> str:
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def channel_times(channel: str) -> list[str]:
    config = json.loads((ROOT / "channels" / channel / "channel.json").read_text(encoding="utf-8"))
    times = config.get("publish_times") or []
    gap = float(config.get("min_post_gap_hours") or 0)
    parsed = [datetime.strptime(t, "%H:%M") for t in times]
    for a, b in zip(parsed, parsed[1:]):
        if (b - a).total_seconds() / 3600 < gap:
            raise ValueError(f"{channel} publish_times {times} 간격이 {gap:g}시간보다 짧음")
    return times


def main() -> None:
    enable = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--enable" else None
    for channel, tasks in TASKS.items():
        times = channel_times(channel)
        if len(times) != len(tasks):
            raise ValueError(f"{channel}: publish_times {times}와 작업 {len(tasks)}개 수가 다름")
        for (name, bat), at in zip(tasks, times):
            exists = ps(f"if (Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue) {{ 'yes' }}") == "yes"
            trigger = f"New-ScheduledTaskTrigger -Daily -At '{at}'"
            if exists:
                ps(f"Set-ScheduledTask -TaskName '{name}' -Trigger ({trigger}) | Out-Null")
            else:
                action = f"New-ScheduledTaskAction -Execute '{ROOT / bat}' -WorkingDirectory '{ROOT}'"
                ps(f"Register-ScheduledTask -TaskName '{name}' -Action ({action}) -Trigger ({trigger}) | Out-Null")
            ps(f"{'Enable' if enable == channel else 'Disable'}-ScheduledTask -TaskName '{name}' | Out-Null")
            state = ps(f"(Get-ScheduledTask -TaskName '{name}').State")
            print(f"{channel:8} {name:22} 매일 {at}  {bat:24} {state}")


if __name__ == "__main__":
    main()

"""채널 설정(channel.json)대로 Windows 작업 스케줄러를 맞춘다(2026-10-31, 11-02 디징 승인 흐름).

- 경제: EconCardNews-Collect(run_collect.bat = auto, 수집~게시 전부, 승인 없음) 하루 1회 publish_times[0](07:30).
  EconCardNews-Publish(18:00 publish)는 쓰지 않는다(비활성 그대로).
- 디징: DijingCardNews-1·2(run_youth.bat = cards, 선정·카드 1건 + 텔레그램 미리보기) publish_times(12:00·19:00).
  DijingCardNews-Publish(run_youth_publish.bat = publish_instagram.py --approved-only) approved_publish_window(09:00~23:00)
  동안 approved_publish_every_min(10)분마다: 다시 만들기 요청 처리 + 승인된 카드만 게시(하루 건수·간격은 posting_slots).

새로 만드는 작업은 "사용 안 함"으로 등록한다. 켜는 건 운영자 승인 뒤 --enable로 채널을 골라 한다.
--enable에 없는 채널 작업은 지금 상태(사용/사용 안 함)를 그대로 둔다.

실행:
    python register_schedule.py                        # 시각·실행 파일만 맞춤(상태 유지)
    python register_schedule.py --enable economy youth # 운영자 승인 후에만
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DAILY_TASKS = {  # 채널 → [(작업 이름, 배치 파일)], publish_times 순서대로
    "economy": [("EconCardNews-Collect", "run_collect.bat")],
    "youth": [("DijingCardNews-1", "run_youth.bat"), ("DijingCardNews-2", "run_youth.bat")],
}
REPEAT_TASKS = {  # 채널 → (작업 이름, 배치 파일). 시간 창·간격은 channel.json
    "youth": ("DijingCardNews-Publish", "run_youth_publish.bat"),
}


def ps(command: str) -> str:
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def channel_config(channel: str) -> dict:
    return json.loads((ROOT / "channels" / channel / "channel.json").read_text(encoding="utf-8"))


def channel_times(config: dict, channel: str) -> list[str]:
    times = config.get("publish_times") or []
    gap = float(config.get("min_post_gap_hours") or 0)
    parsed = [datetime.strptime(t, "%H:%M") for t in times]
    for a, b in zip(parsed, parsed[1:]):
        if (b - a).total_seconds() / 3600 < gap:
            raise ValueError(f"{channel} publish_times {times} 간격이 {gap:g}시간보다 짧음")
    return times


def upsert(name: str, bat: str, trigger: str, enable: bool) -> str:
    """trigger는 $t에 트리거를 넣는 PowerShell 문장."""
    action = f"New-ScheduledTaskAction -Execute '{ROOT / bat}' -WorkingDirectory '{ROOT}'"
    exists = ps(f"if (Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue) {{ 'yes' }}") == "yes"
    if exists:
        ps(f"{trigger}; Set-ScheduledTask -TaskName '{name}' -Action ({action}) -Trigger $t | Out-Null")
    else:  # 새 작업은 사용 안 함으로 시작. 겹쳐 실행되면 새 실행을 버린다(기본값 IgnoreNew)
        ps(f"{trigger}; Register-ScheduledTask -TaskName '{name}' -Action ({action}) -Trigger $t | Out-Null; "
           f"Disable-ScheduledTask -TaskName '{name}' | Out-Null")
    if enable:
        ps(f"Enable-ScheduledTask -TaskName '{name}' | Out-Null")
    return ps(f"(Get-ScheduledTask -TaskName '{name}').State")


def main() -> None:
    enable = set(sys.argv[2:]) if len(sys.argv) > 2 and sys.argv[1] == "--enable" else set()
    for channel, tasks in DAILY_TASKS.items():
        config = channel_config(channel)
        times = channel_times(config, channel)
        if len(times) != len(tasks):
            raise ValueError(f"{channel}: publish_times {times}와 작업 {len(tasks)}개 수가 다름")
        for (name, bat), at in zip(tasks, times):
            state = upsert(name, bat, f"$t = New-ScheduledTaskTrigger -Daily -At '{at}'", channel in enable)
            print(f"{channel:8} {name:24} 매일 {at}  {bat:24} {state}")
        if channel in REPEAT_TASKS:
            name, bat = REPEAT_TASKS[channel]
            start, end = config["approved_publish_window"]
            every = int(config["approved_publish_every_min"])
            minutes = int((datetime.strptime(end, "%H:%M") - datetime.strptime(start, "%H:%M")).total_seconds() // 60) + 1
            trigger = (f"$t = New-ScheduledTaskTrigger -Daily -At '{start}'; "
                       f"$t.Repetition = (New-ScheduledTaskTrigger -Once -At '{start}' -RepetitionInterval "
                       f"(New-TimeSpan -Minutes {every}) -RepetitionDuration (New-TimeSpan -Minutes {minutes})).Repetition")
            state = upsert(name, bat, trigger, channel in enable)
            print(f"{channel:8} {name:24} {start}~{end} {every}분마다  {bat:24} {state}")


if __name__ == "__main__":
    main()

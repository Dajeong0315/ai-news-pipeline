"""카드 이미지(01.png~NN.png, 1080×1350)를 인스타그램 릴스용 9:16 영상(reel.mp4)으로 만든다.

2026-10-30 운영자: 청년 채널은 게시물마다 캐러셀과 릴스를 둘 다 올린다.
- 화면: 같은 카드를 흐리게 키워 세로 배경으로 깔고, 그 위 가운데에 카드를 그대로 얹는다(글자가 잘리지 않게).
- 길이: 장당 SLIDE_SEC초, 장 사이는 부드럽게 겹쳐 넘어간다. 1장짜리는 SINGLE_SEC초.
- 소리: 자동 업로드로는 인스타 음악 라이브러리를 쓸 수 없어, 채널 music/ 폴더의 저작권 무료 음원 중
  하나를 무작위로 골라 영상 길이에 맞춰 자르고 끝을 서서히 줄인다. 폴더가 비면 무음.
  (음원 파일은 공개 저장소에 올리지 않는다 — .gitignore)

실행(확인용):
    python reel.py output/2026-10-30/youth_support-123 [music_dir]
"""

import logging
import random
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("reel")

WIDTH, HEIGHT = 1080, 1920
SLIDE_SEC = 3.5
SINGLE_SEC = 6.0
FADE_SEC = 0.5
MUSIC_EXT = {".mp3", ".m4a", ".aac", ".wav"}


def pick_music(music_dir: Path | None) -> Path | None:
    if not music_dir or not music_dir.is_dir():
        return None
    tracks = [p for p in music_dir.iterdir() if p.suffix.lower() in MUSIC_EXT]
    return random.choice(tracks) if tracks else None


def make_reel(out_dir: Path, music_dir: Path | None = None) -> Path:
    pngs = sorted(out_dir.glob("[0-9][0-9].png"))
    if not pngs:
        raise ValueError(f"카드 이미지 없음: {out_dir}")
    durations = [SINGLE_SEC] if len(pngs) == 1 else [SLIDE_SEC] * len(pngs)
    # 겹쳐 넘어가는 만큼 전체 길이가 줄어든다
    total = sum(durations) - FADE_SEC * (len(pngs) - 1)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for png, sec in zip(pngs, durations):
        cmd += ["-loop", "1", "-t", f"{sec}", "-i", str(png)]
    music = pick_music(music_dir)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    filters = []
    for i in range(len(pngs)):
        filters.append(
            f"[{i}:v]split[b{i}][f{i}];"
            f"[b{i}]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
            f"boxblur=40:2,eq=brightness=-0.06[bb{i}];"
            f"[bb{i}][f{i}]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p,setsar=1[s{i}]"
        )
    last = "s0"
    offset = 0.0
    for i in range(1, len(pngs)):
        offset += durations[i - 1] - FADE_SEC
        filters.append(f"[{last}][s{i}]xfade=transition=fade:duration={FADE_SEC}:offset={offset:.2f}[x{i}]")
        last = f"x{i}"
    if music:
        a = len(pngs)
        filters.append(f"[{a}:a]atrim=0:{total:.2f},afade=t=in:d=0.5,afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5,volume=0.8[aout]")

    out = out_dir / "reel.mp4"
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    if music:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "30", "-movflags", "+faststart", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {result.stderr.strip()[-400:]}")
    log.info("릴스 영상 %s (%.1f초, 음원 %s)", out, total, music.name if music else "없음")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    target = Path(sys.argv[1])
    print(make_reel(target, Path(sys.argv[2]) if len(sys.argv) > 2 else None))

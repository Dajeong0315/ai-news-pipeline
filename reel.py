"""카드 이미지(01.png~NN.png, 1080×1350)를 인스타그램 릴스용 9:16 영상(reel.mp4)으로 만든다.

2026-10-30 운영자: 청년 채널은 게시물마다 캐러셀과 릴스를 둘 다 올린다.
2026-10-31 경제 채널도 릴스 재개(7~8월 릴스가 유일한 비팔로워 유입이었음). 정지 카드 슬라이드쇼는 "대부분 텍스트인 릴스"로
노출이 줄 수 있어 간단한 모션을 넣는다:
- 도입(INTRO_SEC): 첫 1초 안에 핵심 숫자가 0에서 카운트업(덱의 stat 값 → compare 값 → 표지 숫자 순).
  숫자가 없는 덱은 표지 주제 문구(props.cover_tiles)나 표지 제목 키워드를 크게 띄우며 확대. 덱에 있는 글자만 쓰므로 원문에 없는 숫자는 나오지 않는다.
  채널 reel_intro "dday"(디징)는 카운트업 대신 캡션의 D-n을 크게. 설명줄은 cover_tiles → stat/compare label(표지 문장 반복 안 함).
  도입 글자는 1장과 겹치기 전에 사라진다(2026-11-01 첫 디징 릴스에서 도입 글자와 표지 글자가 겹쳐 보임).
- 카드: 같은 카드를 흐리게 키워 세로 배경으로 깔고, 그 위 가운데 카드를 천천히 확대(ZOOM)한다(글자가 잘리지 않을 만큼).
- 길이: 장수에 맞춰 전체 MIN_SEC~MAX_SEC초(1~2장은 7.5초, 5장 이상은 14.5초). 장 사이는 부드럽게 겹쳐 넘어간다.
- 소리: 자동 업로드로는 인스타 음악 라이브러리를 쓸 수 없어, 채널 music/ 폴더의 저작권 무료 음원 중
  하나를 무작위로 골라 영상 길이에 맞춰 자르고 끝을 서서히 줄인다. 폴더가 비면 무음.
  (음원 파일은 공개 저장소에 올리지 않는다 — .gitignore)

실행(확인용):
    python reel.py output/2026-10-30/youth_support-123 [music_dir]
"""

import json
import logging
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("reel")

WIDTH, HEIGHT = 1080, 1920
FPS = 30
INTRO_SEC = 1.5  # 글자 0~0.85초(카운트업 0.6초 + 멈춤) → 0.25초 사라짐 → 1.1~1.5초 배경만 남은 채 카드와 겹침
COUNT_SEC = 0.6  # 도입 중 숫자가 다 올라가는 시간(나머지는 멈춰 보여줌)
FADE_SEC = 0.4
TEXT_OUT_SEC = 0.25  # 도입 글자가 사라지는 시간. 카드와 겹치기(FADE_SEC) 전에 끝난다
ZOOM = 0.06  # 카드 한 장 동안 6% 확대
MIN_SEC, MAX_SEC = 7.0, 15.0
SEC_PER_CARD = 2.6
MUSIC_EXT = {".mp3", ".m4a", ".aac", ".wav"}
FONT_DIRS = (Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_YEAR_RE = re.compile(r"^(19|20)\d\d$")
_DATE_UNIT_RE = re.compile(r"\s*(일|월|시|분|년|주|D-)")
_DDAY_RE = re.compile(r"D-(\d+)")


def pick_music(music_dir: Path | None) -> Path | None:
    if not music_dir or not music_dir.is_dir():
        return None
    tracks = [p for p in music_dir.iterdir() if p.suffix.lower() in MUSIC_EXT]
    return random.choice(tracks) if tracks else None


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for folder in FONT_DIRS:
        for candidate in (name, "malgunbd.ttf"):
            if (folder / candidate).exists():
                return ImageFont.truetype(str(folder / candidate), size)
    return ImageFont.load_default(size)


def key_number(deck: dict) -> tuple[str, str] | None:
    """(숫자 표기, 설명). stat 값 → compare 첫 값 → 표지 문구 속 숫자. 연도(2026 등)만 있는 값은 건너뛴다."""
    slides = deck.get("slides") or []
    cover = slides[0] if slides else {}
    cover_text = " ".join(str(cover.get(k) or "") for k in ("title", "subtitle")).replace("\n", " ")
    found = [(s.get("value"), s.get("label")) for s in slides if s.get("kind") == "stat"]
    found += [(i.get("value"), i.get("label")) for s in slides if s.get("kind") == "compare"
              for i in (s.get("items") or [])[:1] if isinstance(i, dict)]
    # 표지 숫자는 설명줄 없이(표지 문장은 바로 다음 1장에 그대로 나온다)
    found += [(m.group(0) + cover_text[m.end():].split(" ")[0], "") for m in _NUM_RE.finditer(cover_text)]
    for value, label in found:
        value = str(value or "").strip()
        m = _NUM_RE.search(value)
        if (m and not _YEAR_RE.match(m.group(0).replace(",", "")) and len(value) <= 12
                and not _DATE_UNIT_RE.match(value[m.end():])):  # "29일"·"3월"은 카운트업할 숫자가 아니다
            return value, str(label or "").replace("\n", " ").strip()
    return None


def _count_text(value: str, progress: float) -> str:
    """"53.7%"를 progress(0~1)만큼 올라간 값으로. 소수 자릿수·쉼표 표기는 원래 값과 같게."""
    m = _NUM_RE.search(value)
    raw = m.group(0)
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    target = float(raw.replace(",", ""))
    current = target if progress >= 1 else target * progress
    text = f"{current:,.{decimals}f}" if "," in raw else f"{current:.{decimals}f}"
    return value[: m.start()] + text + value[m.end():]


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int = 2) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if font.getlength(trial) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def _fit_font(name: str, text: str, start: int, width: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 60 and _font(name, size).getlength(text) > width:
        size -= 8
    return _font(name, size)


def intro_label(deck: dict) -> str:
    """도입 설명줄로 쓸 표지 주제 문구(props.cover_tiles, "\n"은 띄어쓰기로). 없으면 빈 문자열."""
    return " ".join(str((deck.get("props") or {}).get("cover_tiles") or "").split())


def caption_dday(out_dir: Path, deck: dict) -> str | None:
    """캡션의 "D-n"(공고 카드는 게시 당일 기준으로 캡션에 넣는다). caption.txt가 있으면 그것(운영자가 고친 최종본), 없으면 덱 캡션."""
    path = out_dir / "caption.txt"
    caption = path.read_text(encoding="utf-8") if path.exists() else str(deck.get("caption") or "")
    m = _DDAY_RE.search(caption)
    return f"D-{m.group(1)}" if m else None


def make_intro(cover_png: Path, deck: dict, frames_dir: Path, dday: str | None = None) -> int:
    """도입 프레임(PNG)을 frames_dir에 쓰고 프레임 수를 돌려준다.
    dday가 있으면(디징 reel_intro "dday") 카운트업 없이 D-n을 크게, 아니면 핵심 숫자 카운트업(경제), 숫자도 없으면 키워드.
    설명줄은 props.cover_tiles → stat/compare label 순. 표지 title·subtitle 문장은 바로 다음 1장에 그대로 있으니 되풀이하지 않는다.
    글자는 카드와 겹쳐 넘어가는 구간(FADE_SEC) 전에 TEXT_OUT_SEC 동안 사라져, 겹치는 동안에는 흐린 배경만 남는다(2026-11-01 디징 첫 릴스)."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(cover_png) as cover:
        bg = cover.convert("RGB")
    scale = max(WIDTH / bg.width, HEIGHT / bg.height)
    bg = bg.resize((round(bg.width * scale), round(bg.height * scale)))
    left, top = (bg.width - WIDTH) // 2, (bg.height - HEIGHT) // 2
    bg = bg.crop((left, top, left + WIDTH, top + HEIGHT)).filter(ImageFilter.GaussianBlur(36))
    bg = Image.blend(bg, Image.new("RGB", bg.size, (12, 14, 20)), 0.55)

    tiles = intro_label(deck)
    number = None if dday else key_number(deck)
    slides = deck.get("slides") or [{}]
    if dday:
        big_text, label, mode = dday, tiles, "dday"
    elif number:
        big_text, label, mode = number[0], tiles or number[1], "count"
    else:  # 숫자 없는 덱: 주제 문구가 있으면 그것, 없으면 표지 제목 키워드(경제 기존 동작)
        big_text, label, mode = tiles or str(slides[0].get("title") or "").replace("\n", " ").strip(), "", "keyword"
    big_color = (255, 255, 255) if mode == "keyword" else (255, 214, 10)
    # 숫자·D-n은 한 줄에 맞춰 크게, 키워드는 큰 글씨로 줄바꿈(가장 긴 낱말만 한 줄에 들어가면 됨)
    longest = max(big_text.split(), key=len) if big_text.split() else big_text
    big_font = _fit_font("Pretendard-ExtraBold.ttf", longest if mode == "keyword" else big_text,
                         128 if mode == "keyword" else 240, WIDTH - 140)
    label_font = _font("Pretendard-Bold.ttf", 56)
    label_lines = _wrap(label, label_font, WIDTH - 200) if label else []
    text_end = INTRO_SEC - FADE_SEC  # 여기부터 카드가 겹쳐 들어온다 → 그 전에 글자를 다 지운다
    total = round(INTRO_SEC * FPS)
    for f in range(total):
        t = f / FPS
        progress = min(t / COUNT_SEC, 1.0)
        ease = 1 - (1 - progress) ** 3
        text_alpha = min(max((text_end - t) / TEXT_OUT_SEC, 0.0), 1.0)
        frame = bg.copy()
        if text_alpha > 0:
            layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            text = _count_text(big_text, ease) if mode == "count" else big_text
            lines = _wrap(text, big_font, WIDTH - 140, 3)
            line_h = big_font.size * 1.15
            y = HEIGHT / 2 - (len(lines) * line_h + len(label_lines) * 76) / 2
            for line in lines:
                draw.text((WIDTH / 2 + 4, y + 6), line, font=big_font, fill=(0, 0, 0, 120), anchor="mt")
                draw.text((WIDTH / 2, y), line, font=big_font, fill=big_color, anchor="mt")
                y += line_h
            y += 24
            for line in label_lines:
                draw.text((WIDTH / 2, y), line, font=label_font, fill=(255, 255, 255, round(255 * ease)), anchor="mt")
                y += 76
            # 키워드는 작게 시작해 확대, 숫자·D-n은 살짝 튀어나왔다 자리 잡기
            zoom = 1.12 - 0.12 * ease if mode == "keyword" else 1.0 + 0.06 * (1 - ease)
            if zoom != 1.0:
                w, h = round(WIDTH * zoom), round(HEIGHT * zoom)
                layer = layer.resize((w, h)).crop(((w - WIDTH) // 2, (h - HEIGHT) // 2, (w - WIDTH) // 2 + WIDTH, (h - HEIGHT) // 2 + HEIGHT))
            if text_alpha < 1:
                layer.putalpha(layer.getchannel("A").point(lambda a: round(a * text_alpha)))
            frame.paste(layer, (0, 0), layer)
        frame.save(frames_dir / f"{f:03d}.png")
    return total


def plan_durations(count: int) -> tuple[float, float]:
    """(카드 한 장 길이, 전체 길이). 전체 = 도입 + 카드들 - 겹침, MIN_SEC+0.5 ~ MAX_SEC-0.5초 사이."""
    total = min(max(INTRO_SEC + SEC_PER_CARD * count, MIN_SEC + 0.5), MAX_SEC - 0.5)
    return (total - INTRO_SEC + FADE_SEC * count) / count, total


def duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, timeout=60)
    return float(out.stdout.strip() or 0)


def make_reel(out_dir: Path, music_dir: Path | None = None, deck: dict | None = None, intro: str | None = None) -> Path:
    """intro: 도입 방식. None이면 채널 설정 reel_intro("dday" = 캡션 D-n, 없으면 숫자 카운트업)."""
    pngs = sorted(out_dir.glob("[0-9][0-9].png"))
    if not pngs:
        raise ValueError(f"카드 이미지 없음: {out_dir}")
    if deck is None and (out_dir / "deck.json").exists():
        deck = json.loads((out_dir / "deck.json").read_text(encoding="utf-8"))
    deck = deck or {}
    if intro is None:
        import config  # 확인용 단독 실행(python reel.py)에서도 CHANNEL 환경변수로 채널 설정을 읽는다
        intro = config.CHANNEL_CONFIG.get("reel_intro", "number")
    dday = caption_dday(out_dir, deck) if intro == "dday" else None
    card_sec, total = plan_durations(len(pngs))
    card_frames = round(card_sec * FPS)
    frames_dir = out_dir / "_intro"
    shutil.rmtree(frames_dir, ignore_errors=True)
    make_intro(pngs[0], deck, frames_dir, dday=dday)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames_dir / "%03d.png")]
    for png in pngs:  # 장마다 입력 2개: 흐린 배경용(반복) + 확대용(한 장)
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{card_sec:.3f}", "-i", str(png), "-i", str(png)]
    music = pick_music(music_dir)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    norm = f"fps={FPS},format=yuv420p,setsar=1,settb=AVTB"
    filters = [f"[0:v]scale={WIDTH}:{HEIGHT},{norm}[s0]"]
    for i in range(1, len(pngs) + 1):
        bg, fg = 2 * i - 1, 2 * i
        filters.append(
            f"[{bg}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
            f"boxblur=40:2,eq=brightness=-0.06[bb{i}];"
            # 확대 떨림을 줄이려고 2배로 키운 뒤 zoompan으로 1080×1350을 잘라낸다
            f"[{fg}:v]scale=2160:2700,zoompan=z='1+{ZOOM}*on/{card_frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={card_frames}:s=1080x1350:fps={FPS}[f{i}];"
            f"[bb{i}][f{i}]overlay=(W-w)/2:(H-h)/2:shortest=1,trim=duration={card_sec:.3f},setpts=PTS-STARTPTS,{norm}[s{i}]"
        )
    last, offset = "s0", INTRO_SEC - FADE_SEC
    for i in range(1, len(pngs) + 1):
        filters.append(f"[{last}][s{i}]xfade=transition=fade:duration={FADE_SEC}:offset={offset:.3f}[x{i}]")
        last, offset = f"x{i}", offset + card_sec - FADE_SEC
    if music:
        a = 2 * len(pngs) + 1
        filters.append(f"[{a}:a]atrim=0:{total:.2f},afade=t=in:d=0.5,afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5,volume=0.8[aout]")

    out = out_dir / "reel.mp4"
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    if music:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-movflags", "+faststart", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    shutil.rmtree(frames_dir, ignore_errors=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {result.stderr.strip()[-400:]}")
    log.info("릴스 영상 %s (%.1f초, 도입 %s, 음원 %s)", out, total,
             dday or ("숫자 카운트업" if key_number(deck) else "키워드 강조"), music.name if music else "없음")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    target = Path(sys.argv[1])
    print(make_reel(target, Path(sys.argv[2]) if len(sys.argv) > 2 else None))

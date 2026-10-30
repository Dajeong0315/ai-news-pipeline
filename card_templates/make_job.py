"""브랜드 설정 + 슬라이드 덱 + 배경 이미지를 묶어 render.js용 작업 파일을 만들고 렌더한다.

    python card_templates/make_job.py <template>[:plain] <deck.json> <brand.json> <background> <out_dir>

- 템플릿 이름 뒤에 `:plain`을 붙이면 마스코트 없는 버전으로 렌더한다(예: `moody:plain`).

- brand.json의 마스코트 경로는 brand.json 위치 기준으로 풀어서 절대경로로 넣는다.
- 렌더 결과(01.png …)와 미리보기 한 장(sheet.jpg)을 out_dir에 만든다.
"""

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def split_variant(spec: str) -> tuple[str, bool]:
    """"moody:plain" → ("moody", False). 뒤에 아무것도 없으면 마스코트 있는 버전."""
    name, _, variant = spec.partition(":")
    return name, variant != "plain"


def build_job(template: str, deck_path: Path, brand_path: Path, background: Path) -> dict:
    template, mascot = split_variant(template)
    deck = json.loads(deck_path.read_text(encoding="utf-8"))
    brand = json.loads(brand_path.read_text(encoding="utf-8"))
    brand["mascots"] = {k: str((brand_path.parent / v).resolve()) for k, v in brand.get("mascots", {}).items()}
    return {"template": template, "mascot": mascot, "brand": brand, "background": str(background.resolve()),
            "slides": deck["slides"], "props": deck.get("props") or {}}


def contact_sheet(out_dir: Path, cols: int = 3, width: int = 360) -> Path:
    pngs = sorted(out_dir.glob("[0-9][0-9].png"))
    h = int(width * 1350 / 1080)
    rows = (len(pngs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * width + (cols - 1) * 12, rows * h + (rows - 1) * 12), "white")
    for i, p in enumerate(pngs):
        sheet.paste(Image.open(p).convert("RGB").resize((width, h)), ((i % cols) * (width + 12), (i // cols) * (h + 12)))
    path = out_dir / "sheet.jpg"
    sheet.save(path, quality=85)
    return path


if __name__ == "__main__":
    template, deck, brand, bg, out = sys.argv[1:6]
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(build_job(template, Path(deck), Path(brand), Path(bg)), ensure_ascii=False, indent=1), encoding="utf-8")
    result = subprocess.run(["node", str(HERE / "render.js"), str(job_path), str(out_dir)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((result.stdout + result.stderr).strip())
    print(contact_sheet(out_dir))
    sys.exit(result.returncode)

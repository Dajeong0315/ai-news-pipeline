"""템플릿 배경 이미지를 Cloudflare Workers AI(flux-1-schnell)로 만든다.

프로젝트(묘한 경제 등)와 무관하게 쓰도록 환경변수만 읽는다:
    CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, (선택) IMAGE_GEN_MODEL

템플릿마다 사진 분위기가 달라서(README의 "배경 이미지" 항목), 주제를 설명하는
subject 문장 뒤에 템플릿별 STYLE_SUFFIX를 붙여 요청한다.

실행:
    python card_templates/images.py "<subject>" <out_dir> [template ...]
"""

import base64
import os
import sys
from pathlib import Path

import httpx

MODEL = os.environ.get("IMAGE_GEN_MODEL", "@cf/black-forest-labs/flux-1-schnell")

STYLE_SUFFIX = {
    "gallery": "as a 19th-century European oil painting, visible brush strokes, museum masterpiece, warm muted palette, dramatic chiaroscuro",
    "moody": "moody editorial lifestyle photo, desaturated warm grey tones, soft window light, minimal, film grain, shallow depth of field",
    "sticker": "bright airy flat-lay photo on a light grey linen surface, soft daylight, pastel accents, lots of empty space in the center",
    "darkcenter": "dark cinematic photograph, low key lighting, deep shadows, fog, single light source, cool tones, lots of negative space",
    "blocktitle": "candid documentary street photograph, natural daylight, vivid but realistic colors, 35mm",
    "cutout": "close-up flat texture background, softly out of focus, slightly desaturated, evenly lit, no people",
}
NEGATIVE = "no text, no letters, no logos, no watermark"


def generate(prompt: str, out_path: Path, client: httpx.Client) -> Path:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/run/{MODEL}"
    )
    resp = client.post(url, json={"prompt": f"{prompt}. {NEGATIVE}", "steps": 8}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"이미지 생성 실패: {data.get('errors')}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data["result"]["image"]))
    return out_path


def generate_for_templates(subject: str, out_dir: Path, templates: list[str]) -> dict[str, Path]:
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}
    # 같은 클라이언트를 재사용해 연결을 한 번만 맺는다.
    with httpx.Client(headers=headers, transport=httpx.HTTPTransport(local_address="0.0.0.0")) as client:
        return {
            name: generate(f"{subject}, {STYLE_SUFFIX[name]}", out_dir / f"bg-{name}.jpg", client)
            for name in templates
        }


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    subject, out = sys.argv[1], Path(sys.argv[2])
    names = sys.argv[3:] or list(STYLE_SUFFIX)
    for name, path in generate_for_templates(subject, out, names).items():
        print(name, path)

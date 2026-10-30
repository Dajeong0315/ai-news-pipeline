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
    # 두 번째 채널(청년) 템플릿용(2026-10-30). chat·notify는 사진을 쓰지 않지만 파이프라인이 배경을 한 장 만들므로 둔다.
    "chat": "minimal dark abstract background, deep black, subtle soft glow",
    "notify": "soft colorful abstract gradient wallpaper",
    "desktop": "bright tidy desk setup photo with a laptop and small gadgets, soft natural window light, warm minimal tones",
    # soft는 누끼처럼 쓰려고 흰 배경 위 피사체 하나를 요청한다(템플릿이 곱하기로 흰 부분을 지운다).
    "soft": "single subject, full body, isolated on a pure white seamless studio background, soft even light, centered, lots of white margin",
    "edit": "warm film lifestyle photo, soft natural light, muted beige and cream tones, cozy everyday scene, gentle grain",
    "airdrop": "candid point-of-view lifestyle snapshot, soft overcast daylight, muted warm tones, everyday moment with a phone or coffee",
    # folder는 한 장을 왼쪽·가운데·오른쪽 세 구도로 잘라 쓰므로 가로로 넓게 볼거리가 있는 장면이 좋다.
    "folder": "wide candid snapshot with several interesting details spread across the frame, bright natural light, vivid colors",
    "magazine": "editorial magazine photograph, shallow depth of field, bokeh city lights, clean modern tones",
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

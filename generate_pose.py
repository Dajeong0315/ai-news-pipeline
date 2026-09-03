"""브랜드 캐릭터(고양이) 포즈가 목록에 없을 때 AI로 새로 만든다.

brand-guide.md의 캐릭터 설명을 고정 프리픽스로 붙여 스타일 일관성을 최대한
유지하고, 크로마키(순수 그린 배경)로 생성한 뒤 Pillow로 배경을 투명 처리해
기존 포즈들과 같은 방식(투명 PNG, 카드 위에 절대좌표로 얹힘)으로 저장한다.

기존 18종처럼 손으로 다듬은 결과와 그림체가 100% 동일하다는 보장은 없다
(AI 이미지 생성의 근본적 한계) — grill-me에서 사용자가 이 리스크를 인지하고
"바로 자동 사용"하기로 결정함.

실행(단독 테스트용):
    python generate_pose.py "a cat looking worried at a falling stock chart"
"""

import base64
import io
import logging
import re
import sys
from pathlib import Path

from PIL import Image

import config
from http_retry import post_with_retry

log = logging.getLogger("generate_pose")

CHARACTER_DIR = Path("assets/character")

CHARACTER_STYLE_PREFIX = (
    "A single cute chibi cat mascot character, flat 2D illustration sticker style, "
    "cream-white fur with light brown tabby markings, big round eyes, rosy blushed cheeks, "
    "round soft silhouette, soft brown outline, no shading gradients, centered, "
    "solid pure green chroma-key background (#00FF00), no other objects, no text: "
)


def slugify(description: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")
    return f"cat-{slug[:30]}"


def _call_pose_model(prompt: str) -> bytes:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{config.POSE_GEN_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    resp = post_with_retry(url, headers=headers, json={"prompt": prompt, "steps": 4}, timeout=60)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return resp.content

    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"포즈 이미지 생성 실패: {data.get('errors')}")
    return base64.b64decode(data["result"]["image"])


def chroma_key_to_transparent(image_bytes: bytes) -> Image.Image:
    """녹색 계열이면 투명 처리한다. 생성 모델이 요청한 순수 단색 배경
    대신 그라데이션·비네트가 섞인 녹색 배경을 주는 경우가 많아(실측 확인),
    RGB 유클리드 거리 대신 HSV 색상(Hue) 기준으로 판단해 밝기/채도가
    달라도 "초록색 계열"이면 넓게 잡아낸다. 캐릭터는 크림/갈색/분홍
    계열이라 초록 색조가 없으므로 안전하다."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    hsv = img.convert("HSV")
    rgb_pixels = img.load()
    hsv_pixels = hsv.load()
    for y in range(img.height):
        for x in range(img.width):
            h, s, v = hsv_pixels[x, y]
            # Pillow의 H는 0~255 스케일(0~360도 매핑). 초록은 대략 120도 근방
            # -> 255*120/360 ≈ 85. 채도가 너무 낮으면(흰/회색) 초록으로 오판할
            # 수 있어 최소 채도 조건도 함께 건다.
            if 50 <= h <= 130 and s >= 40:
                r, g, b, a = rgb_pixels[x, y]
                rgb_pixels[x, y] = (r, g, b, 0)
    return img


def generate_new_pose(description: str) -> str:
    """설명을 받아 새 포즈 PNG를 assets/character/에 저장하고 포즈 이름(확장자 제외)을 반환한다."""
    pose_name = slugify(description)
    out_path = CHARACTER_DIR / f"{pose_name}.png"
    if out_path.exists():
        return pose_name  # 이미 같은 설명으로 만든 적 있으면 재생성하지 않고 재사용

    prompt = CHARACTER_STYLE_PREFIX + description
    log.info("새 포즈 생성 중: %s -> %s", description, pose_name)
    image_bytes = _call_pose_model(prompt)
    transparent = chroma_key_to_transparent(image_bytes)
    CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    transparent.save(out_path, "PNG")
    log.info("새 포즈 저장 완료: %s", out_path)
    return pose_name


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) != 2:
        print('사용법: python generate_pose.py "설명(영어 권장)"')
        sys.exit(1)
    name = generate_new_pose(sys.argv[1])
    print(name)

"""GLM이 만든 영어 프롬프트로 Cloudflare Workers AI(FLUX)에서 배경 이미지를 생성한다.

image_prompts 테이블에 프롬프트가 있지만 아직 generated_images가 없는 뉴스에 대해
이미지를 생성하고 output/YYYY-MM-DD/backgrounds/{news_item_id}.png로 저장한다.

verify_image.py의 재시도 경로에서도 generate_and_store()를 그대로 재사용한다.

실행:
    python generate_image.py
"""

import base64
import logging
from datetime import date
from pathlib import Path

import httpx

import config
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate_image")

FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"


def output_dir(for_date: date | None = None) -> Path:
    d = (for_date or date.today()).isoformat()
    path = Path("output") / d / "backgrounds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def call_flux(prompt_text: str) -> bytes:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{FLUX_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    resp = httpx.post(url, headers=headers, json={"prompt": prompt_text, "steps": 4}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"FLUX 생성 실패: {data.get('errors')}")
    b64_image = data["result"]["image"]
    return base64.b64decode(b64_image)


def generate_and_store(news_item_id: int, prompt_text: str, retry_count: int = 0) -> dict:
    client = get_client()
    image_bytes = call_flux(prompt_text)

    filename = f"{news_item_id}_{retry_count}.png" if retry_count else f"{news_item_id}.png"
    path = output_dir() / filename
    path.write_bytes(image_bytes)

    row = (
        client.table("generated_images")
        .insert(
            {
                "news_item_id": news_item_id,
                "image_path": str(path),
                "retry_count": retry_count,
            }
        )
        .execute()
        .data[0]
    )
    log.info("[id=%s] 이미지 생성 완료: %s", news_item_id, path)
    return row


def fetch_pending() -> list[dict]:
    client = get_client()
    prompts = client.table("image_prompts").select("id,news_item_id,prompt_text").execute().data or []
    if not prompts:
        return []

    existing = (
        client.table("generated_images")
        .select("news_item_id")
        .in_("news_item_id", [p["news_item_id"] for p in prompts])
        .execute()
        .data
        or []
    )
    done_ids = {row["news_item_id"] for row in existing}
    return [p for p in prompts if p["news_item_id"] not in done_ids]


def main():
    pending = fetch_pending()
    log.info("이미지 생성 대상: %d건", len(pending))
    for p in pending:
        try:
            generate_and_store(p["news_item_id"], p["prompt_text"])
        except Exception as e:
            log.error("[id=%s] 이미지 생성 실패: %s", p["news_item_id"], e)


if __name__ == "__main__":
    main()

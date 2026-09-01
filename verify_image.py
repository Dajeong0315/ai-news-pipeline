"""생성된 배경 이미지가 뉴스 내용과 부적절하게 어긋나지 않는지 비전 모델로 검증한다.

기본 모델은 config.VISION_MODEL(@cf/meta/llama-3.2-11b-vision-instruct).
원래 스펙은 "Gemma Vision"을 지정했으나 이 Cloudflare 계정 카탈로그에 Gemma
비전 모델이 없어(GET /ai/models/search로 확인) 계정에서 실제로 쓸 수 있는
멀티모달 모델로 대체했다.

실패 시 최대 config.MAX_IMAGE_RETRIES(기본 1)회, 프롬프트 재생성 -> 이미지 재생성
-> 재검증을 수행한다. 재실패하면 텔레그램으로 운영자에게 알림만 보내고 해당
뉴스의 카드는 건너뛴다 (vision_check_passed=false로 남고 compose_card.py가 스킵).

실행:
    python verify_image.py
"""

import logging
import re

import config
import telegram_client
from db import get_client
from generate_image import generate_and_store
from generate_prompt import call_llm
from http_retry import post_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("verify_image")

VISION_PROMPT_TEMPLATE = (
    "This image will be used as the background of a professional economic/stock news card "
    "(category: {category}). Question: is this image appropriate and professional-looking, "
    "with no violent, offensive, sexual, or bizarre/nonsensical content? "
    "Answer with a single word: yes or no."
)


def fetch_unverified() -> list[dict]:
    client = get_client()
    resp = (
        client.table("generated_images")
        .select("id,news_item_id,image_path,retry_count,news_items(title,category)")
        .is_("vision_check_passed", "null")
        .execute()
    )
    return resp.data or []


def call_vision_model(image_path: str, title: str, category: str) -> tuple[bool, str]:
    image_bytes = open(image_path, "rb").read()
    prompt = VISION_PROMPT_TEMPLATE.format(category=category)

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{config.VISION_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    body = {"image": list(image_bytes), "prompt": prompt, "max_tokens": 50}
    resp = post_with_retry(url, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"비전 모델 호출 실패: {data.get('errors')}")

    text = data["result"]["description"].strip()
    lowered = text.lower()
    has_no = re.search(r"\bno\b", lowered) is not None
    has_yes = re.search(r"\byes\b", lowered) is not None
    # LLaVA는 자유 서술형으로 답하는 경향이 있어, 'no'만 단독으로 등장하는
    # 경우에만 부적합으로 판단(모호하면 통과시키는 안전한 기본값).
    passed = not (has_no and not has_yes)
    return passed, text


def notify_failure(title: str):
    telegram_client.send_message(f"⚠️ 이미지 검증 재실패로 카드 생성을 건너뜁니다.\n{title}")


def verify_one(row: dict):
    client = get_client()
    news = row.get("news_items") or {}
    title = news.get("title", "")
    category = news.get("category", "")

    try:
        passed, note = call_vision_model(row["image_path"], title, category)
    except Exception as e:
        log.error("[image_id=%s] 비전 모델 호출 실패: %s", row["id"], e)
        return

    client.table("generated_images").update(
        {"vision_check_passed": passed, "vision_check_note": note}
    ).eq("id", row["id"]).execute()

    if passed:
        log.info("[image_id=%s] 검증 통과", row["id"])
        return

    log.warning("[image_id=%s] 검증 실패: %s", row["id"], note)

    if row["retry_count"] >= config.MAX_IMAGE_RETRIES:
        notify_failure(title)
        return

    try:
        new_prompt = call_llm(title, category)
        client.table("image_prompts").insert(
            {"news_item_id": row["news_item_id"], "prompt_text": new_prompt}
        ).execute()

        # 재시도는 같은 모델이 또 실패할 가능성을 줄이기 위해 대체 FLUX 모델을 사용한다
        new_row = generate_and_store(
            row["news_item_id"],
            new_prompt,
            retry_count=row["retry_count"] + 1,
            model=config.FLUX_FALLBACK_MODEL,
        )
        retry_passed, retry_note = call_vision_model(new_row["image_path"], title, category)
        client.table("generated_images").update(
            {"vision_check_passed": retry_passed, "vision_check_note": retry_note}
        ).eq("id", new_row["id"]).execute()

        if retry_passed:
            log.info("[news_item_id=%s] 재시도 검증 통과", row["news_item_id"])
        else:
            log.warning("[news_item_id=%s] 재시도 검증도 실패", row["news_item_id"])
            notify_failure(title)
    except Exception as e:
        log.error("[news_item_id=%s] 재시도 처리 실패: %s", row["news_item_id"], e)
        notify_failure(title)


def main():
    rows = fetch_unverified()
    log.info("검증 대상: %d건", len(rows))
    for row in rows:
        verify_one(row)


if __name__ == "__main__":
    main()

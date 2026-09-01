"""승인된 뉴스(status='approved')를 GLM API로 영어 이미지 프롬프트로 변환한다.

카드 뉴스 배경 이미지 생성용 프롬프트이므로, 텍스트/글자가 들어가지 않는
추상적·상징적 배경 이미지를 요청하도록 시스템 프롬프트를 구성한다.

실행:
    python generate_prompt.py
"""

import logging

import httpx

import config
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate_prompt")

SYSTEM_PROMPT = (
    "You are a prompt writer for an AI image generator (FLUX). "
    "Given a Korean economic/stock news headline and its category, write ONE English "
    "image generation prompt for a card-news BACKGROUND image. Rules: "
    "no text, no letters, no numbers, no charts with readable labels; "
    "abstract or symbolic financial/business visual (e.g. skyline, stock market floor, "
    "currency motifs, data flow, corporate architecture) matching the news mood; "
    "professional, modern, high quality, suitable as a background for overlaid title text. "
    "Output ONLY the prompt text, nothing else."
)

CATEGORY_HINT = {
    "index_macro": "macro economy / stock index / currency exchange theme",
    "stock": "individual company / corporate business theme",
    "policy_industry": "central bank policy / industry sector theme",
}


def fetch_approved_without_prompt() -> list[dict]:
    client = get_client()
    approved = (
        client.table("news_items")
        .select("id,title,category")
        .eq("status", "approved")
        .execute()
        .data
        or []
    )
    if not approved:
        return []

    existing = (
        client.table("image_prompts")
        .select("news_item_id")
        .in_("news_item_id", [it["id"] for it in approved])
        .execute()
        .data
        or []
    )
    done_ids = {row["news_item_id"] for row in existing}
    return [it for it in approved if it["id"] not in done_ids]


def call_glm(title: str, category: str) -> str:
    hint = CATEGORY_HINT.get(category, "")
    user_content = f"Headline: {title}\nCategory: {hint}"
    payload = {
        "model": config.GLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {config.GLM_API_KEY}"}
    resp = httpx.post(config.GLM_API_BASE_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def main():
    items = fetch_approved_without_prompt()
    log.info("프롬프트 변환 대상: %d건", len(items))

    client = get_client()
    for item in items:
        try:
            prompt_text = call_glm(item["title"], item["category"])
        except Exception as e:
            log.error("GLM 호출 실패 [id=%s]: %s", item["id"], e)
            continue

        if not prompt_text:
            log.error("빈 프롬프트 반환 [id=%s], 건너뜀", item["id"])
            continue

        client.table("image_prompts").insert(
            {"news_item_id": item["id"], "prompt_text": prompt_text}
        ).execute()
        log.info("[id=%s] 프롬프트 저장 완료: %s", item["id"], prompt_text[:80])


if __name__ == "__main__":
    main()

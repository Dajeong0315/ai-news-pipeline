"""승인된 뉴스(status='approved')를 Cloudflare Workers AI 텍스트 모델로 영어 이미지 프롬프트로 변환한다.

원래 스펙은 GLM API(Zhipu)를 사용하도록 돼 있었으나, GLM의 무료 티어가 결제수단
등록을 요구해 이미 계정이 있는 Cloudflare Workers AI로 대체했다("GLM-4.7-Flash
또는 상응 모델" 허용 문구에 따름). FLUX/Gemma Vision과 같은 계정·토큰을 재사용한다.

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


def call_llm(title: str, category: str) -> str:
    hint = CATEGORY_HINT.get(category, "")
    user_content = f"Headline: {title}\nCategory: {hint}"
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{config.CLOUDFLARE_TEXT_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"프롬프트 생성 실패: {data.get('errors')}")
    return data["result"]["response"].strip()


def main():
    items = fetch_approved_without_prompt()
    log.info("프롬프트 변환 대상: %d건", len(items))

    client = get_client()
    for item in items:
        try:
            prompt_text = call_llm(item["title"], item["category"])
        except Exception as e:
            log.error("프롬프트 생성 호출 실패 [id=%s]: %s", item["id"], e)
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

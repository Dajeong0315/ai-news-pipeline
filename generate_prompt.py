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


def _call_text_model(system_prompt: str, user_content: str, max_tokens: int | None = None) -> str:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{config.CLOUDFLARE_TEXT_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    resp = httpx.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"텍스트 모델 호출 실패: {data.get('errors')}")
    return data["result"]["response"].strip()


def call_llm(title: str, category: str) -> str:
    hint = CATEGORY_HINT.get(category, "")
    user_content = f"Headline: {title}\nCategory: {hint}"
    return _call_text_model(SYSTEM_PROMPT, user_content)


TITLE_SUMMARY_SYSTEM_PROMPT = (
    "너는 경제/주식 카드뉴스 인스타그램 계정의 헤드라인 카피라이터다. 목표는 스크롤을 "
    "멈추고 클릭하고 싶게 만드는 것이다.\n"
    "한국어 뉴스 제목을, 어려운 경제·금융 전문용어 대신 누구나 바로 이해하는 쉬운 "
    "일상 단어로 바꾸고, 핵심 키워드 2~4개만 남긴 짧고 눈에 띄는 헤드라인 구로 다시 써라.\n"
    "가장 중요한 조건: 공백과 숫자, 기호를 모두 포함해 글자 수가 {max_chars}자를 "
    "절대 넘으면 안 된다. 문장이 아니라 구로 끝내고, 부연설명이나 두 번째 문장을 "
    "덧붙이지 마라.\n"
    "예시(15자 제한 기준):\n"
    "  입력: '일본 10년물 금리 2.97%로 급등...30년 만에 최고치 기록'\n"
    "  출력: 日 금리 30년만에 최고\n"
    "  입력: '카카오, 부산서 AI 헬스 서비스 26 공개'\n"
    "  출력: 카카오 AI 헬스 떴다\n"
    "결과는 축약된 제목 텍스트 한 줄만 출력하고, 따옴표·마침표·부연설명은 붙이지 마라."
)


def summarize_title(title: str, category: str, max_chars: int) -> str:
    hint = CATEGORY_HINT.get(category, "")
    system_prompt = TITLE_SUMMARY_SYSTEM_PROMPT.format(max_chars=max_chars)
    user_content = f"원문 제목: {title}\n카테고리: {hint}"
    result = _call_text_model(system_prompt, user_content, max_tokens=24)
    return result.strip("'\" \n。.").splitlines()[0].strip()


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

"""Cloudflare Workers AI 텍스트 모델(config.CLOUDFLARE_TEXT_MODEL) 공용 호출 헬퍼.

원래 GLM API(Zhipu)를 쓰도록 스펙에 적혀 있었으나, GLM 무료 티어가 결제수단
등록을 요구해 이미 계정이 있는 Cloudflare Workers AI로 대체했다("GLM-4.7-Flash
또는 상응 모델" 허용 문구에 따름).
"""

import json

import config
from http_retry import post_with_retry


def call_text_model(system_prompt: str, user_content: str, max_tokens: int | None = None) -> str:
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
    resp = post_with_retry(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"텍스트 모델 호출 실패: {data.get('errors')}")
    response = data["result"]["response"]
    # 프롬프트가 JSON 출력을 요구하면 모델이 문자열 대신 이미 파싱된 객체를
    # 돌려줄 때가 있다(실측으로 확인). 항상 문자열로 통일해서 반환한다.
    if isinstance(response, (dict, list)):
        return json.dumps(response, ensure_ascii=False)
    return response.strip()

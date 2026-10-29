"""Cloudflare Workers AI 텍스트 모델(config.CLOUDFLARE_TEXT_MODEL) 공용 호출 헬퍼.

원래 GLM API(Zhipu)를 쓰도록 스펙에 적혀 있었으나, GLM 무료 티어가 결제수단
등록을 요구해 이미 계정이 있는 Cloudflare Workers AI로 대체했다("GLM-4.7-Flash
또는 상응 모델" 허용 문구에 따름).
"""

import json
import logging

import config
from http_retry import post_with_retry

log = logging.getLogger("llm_client")
NEURONS_USED = 0.0


def call_text_model(
    system_prompt: str, user_content: str, max_tokens: int | None = None, model: str | None = None
) -> str:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{model or config.CLOUDFLARE_TEXT_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    if "gpt-oss" in (model or config.CLOUDFLARE_TEXT_MODEL):
        # OpenAI Responses 형식 모델(2026-10-29 확인): instructions/input으로 받고 output[]으로 돌려준다.
        effort = config.TEXT_MODEL_REASONING_EFFORT
        payload = {"instructions": system_prompt, "input": user_content, "reasoning": {"effort": effort}}
        if max_tokens:
            payload["max_output_tokens"] = max_tokens
        resp = post_with_retry(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        result = resp.json().get("result") or {}
        # 무료 한도(하루 1만 뉴런) 관리용: 호출마다 소모량을 남기고 실행 단위로 누적한다.
        usage = result.get("usage") or {}
        global NEURONS_USED
        NEURONS_USED += usage.get("neurons") or 0
        log.info("텍스트 모델 사용량: in %s / out %s 토큰, %.1f 뉴런 (이번 실행 누적 %.0f)",
                 usage.get("input_tokens"), usage.get("output_tokens"), usage.get("neurons") or 0, NEURONS_USED)
        texts = [
            part.get("text", "")
            for block in result.get("output", []) if block.get("type") == "message"
            for part in block.get("content", []) if part.get("type") == "output_text"
        ]
        if not texts:
            raise RuntimeError(f"텍스트 모델 응답에 본문 없음: status={result.get('status')}")
        return "".join(texts).strip()

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

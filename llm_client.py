"""Cloudflare Workers AI 텍스트 모델(config.CLOUDFLARE_TEXT_MODEL) 공용 호출 헬퍼.

원래 GLM API(Zhipu)를 쓰도록 스펙에 적혀 있었으나, GLM 무료 티어가 결제수단
등록을 요구해 이미 계정이 있는 Cloudflare Workers AI로 대체했다("GLM-4.7-Flash
또는 상응 모델" 허용 문구에 따름).
"""

import json
import logging
import time

import config
from http_retry import post_with_retry

log = logging.getLogger("llm_client")
NEURONS_USED = 0.0
# 응답에 뉴런 수가 없는 모델(messages 형식)의 추정용 단가: 백만 토큰당 (입력, 출력) 뉴런. Cloudflare Workers AI 가격표 기준.
# gpt-oss-120b는 응답의 usage.neurons와 맞는지 확인함(11/2 in 3064·out 6747 → 557.0).
NEURON_RATES = {
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": (26668, 204805),
    "@cf/openai/gpt-oss-120b": (31818, 68182),
    "@cf/openai/gpt-oss-20b": (18182, 27273),
    "@cf/qwen/qwen3-30b-a3b-fp8": (4625, 30475),
}


# --- 429 구분(2026-11-02) ---
# 디징 09:33 429를 "일일 무료 한도 소진"으로 보고 멈췄는데, 그날 디징 계정 사용량은 0/10k였다(대시보드 확인). 429는 하루 한도
# 말고도 모델 용량 부족·속도 제한으로도 온다. 응답 본문으로 하루 한도일 때만 그날 멈추고, 나머지는 30초→2분→5분 기다려
# 다시 부른 뒤 그래도 안 되면 대체 모델로 한 번 더 부른다.
LIMIT_WAITS = (30, 120, 300)
FALLBACK_MODELS = {
    "@cf/openai/gpt-oss-120b": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": "@cf/openai/gpt-oss-120b",
}


class DailyQuotaExceeded(RuntimeError):
    """Cloudflare 일일 무료 한도(1만 뉴런) 소진. 09:00 KST 리셋까지 다시 불러도 소용없다."""


class TemporaryLimit(RuntimeError):
    """하루 한도가 아닌 429(모델 용량 부족·속도 제한). 기다렸다 다시 부르고, 안 되면 대체 모델로."""


def _limit_error(resp) -> tuple[bool, str]:
    """429 응답 본문 → (하루 한도인지, 사람이 읽을 이유)."""
    try:
        errors = resp.json().get("errors") or []
    except ValueError:
        errors = []
    text = " ".join(f"{e.get('code')} {e.get('message')}" for e in errors if isinstance(e, dict)) or resp.text[:300]
    low = text.lower()
    daily = "daily free allocation" in low or ("neuron" in low and ("allocation" in low or "exceeded" in low)) or "3036" in low
    return daily, text.strip()


def _post(url: str, model: str, **kwargs):
    """429를 원인별로 처리하는 호출. 하루 한도면 DailyQuotaExceeded, 일시 제한이 끝까지 풀리지 않으면 TemporaryLimit."""
    for attempt, wait in enumerate((*LIMIT_WAITS, None), 1):
        resp = post_with_retry(url, **kwargs)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        daily, reason = _limit_error(resp)
        account = config.CLOUDFLARE_ACCOUNT_ID[:4]
        if daily:
            log.error("Cloudflare 일일 무료 한도 소진(계정 %s…, %s): %s", account, model, reason)
            raise DailyQuotaExceeded(f"Cloudflare Workers AI 일일 무료 한도 소진(429, {reason}) — 09:00 KST 리셋 후 재시도")
        if wait is None:
            raise TemporaryLimit(f"Cloudflare 일시 제한(429, {reason}) — {len(LIMIT_WAITS)}번 기다려 다시 불러도 풀리지 않음({model})")
        log.warning("Cloudflare 일시 제한(계정 %s…, %s): %s → %d초 뒤 다시(%d/%d)", account, model, reason, wait, attempt, len(LIMIT_WAITS))
        time.sleep(wait)


def _record_usage(model: str, usage: dict) -> None:
    """호출마다 소모량을 남기고 실행 단위로 누적한다(무료 한도 하루 1만 뉴런 관리)."""
    global NEURONS_USED
    tokens_in = usage.get("input_tokens", usage.get("prompt_tokens")) or 0
    tokens_out = usage.get("output_tokens", usage.get("completion_tokens")) or 0
    neurons = usage.get("neurons")
    estimated = neurons is None
    if estimated:
        rate_in, rate_out = NEURON_RATES.get(model, (0, 0))
        neurons = (tokens_in * rate_in + tokens_out * rate_out) / 1e6
    NEURONS_USED += neurons
    log.info("텍스트 모델 사용량(%s, 계정 %s…): in %s / out %s 토큰, %.1f 뉴런%s (이번 실행 누적 %.0f)",
             model.rsplit("/", 1)[-1], config.CLOUDFLARE_ACCOUNT_ID[:4], tokens_in, tokens_out, neurons,
             "(추정)" if estimated else "", NEURONS_USED)


def call_text_model(
    system_prompt: str, user_content: str, max_tokens: int | None = None, model: str | None = None
) -> str:
    model = model or config.CLOUDFLARE_TEXT_MODEL
    try:
        return _call(system_prompt, user_content, max_tokens, model)
    except TemporaryLimit as e:
        fallback = FALLBACK_MODELS.get(model)
        if not fallback:
            raise
        log.warning("%s — 대체 모델 %s로 다시 부름", e, fallback)
        return _call(system_prompt, user_content, max_tokens, fallback)


def _call(system_prompt: str, user_content: str, max_tokens: int | None, model: str) -> str:
    url = f"https://api.cloudflare.com/client/v4/accounts/{config.CLOUDFLARE_ACCOUNT_ID}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    if "gpt-oss" in model:
        # OpenAI Responses 형식 모델(2026-10-29 확인): instructions/input으로 받고 output[]으로 돌려준다.
        effort = config.TEXT_MODEL_REASONING_EFFORT
        payload = {"instructions": system_prompt, "input": user_content, "reasoning": {"effort": effort}}
        if max_tokens:
            payload["max_output_tokens"] = max_tokens
        resp = _post(url, model, json=payload, headers=headers, timeout=120)
        result = resp.json().get("result") or {}
        _record_usage(model, result.get("usage") or {})
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
    resp = _post(url, model, json=payload, headers=headers, timeout=60)
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"텍스트 모델 호출 실패: {data.get('errors')}")
    _record_usage(model, data["result"].get("usage") or {})
    result = data["result"]
    # 2026-11 응답이 OpenAI chat 형식(choices)으로도 온다. response가 없으면 choices에서 꺼낸다.
    response = result["response"] if "response" in result else result["choices"][0]["message"]["content"]
    # 프롬프트가 JSON 출력을 요구하면 모델이 문자열 대신 이미 파싱된 객체를
    # 돌려줄 때가 있다(실측으로 확인). 항상 문자열로 통일해서 반환한다.
    if isinstance(response, (dict, list)):
        return json.dumps(response, ensure_ascii=False)
    return response.strip()

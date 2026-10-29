"""카테고리별로 오늘의 기사 한 건을 자동 선정해 승인 상태로 전환한다."""

import json
import logging
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz

import config
import events
from db import get_client
from llm_client import call_text_model

log = logging.getLogger("auto_select")


def fetch_candidates(category: str) -> list[dict]:
    """오늘 수집·정제된 기사만 평가해 과거 미선정 기사의 재순환을 막는다."""
    since = (datetime.now(timezone.utc) - timedelta(hours=config.MAX_AGE_HOURS)).isoformat()
    response = (
        get_client().table("news_items")
        .select("id,title,summary,source,url,category,published_at,collected_at")
        .eq("category", category)
        .eq("status", "filtered")
        .gte("collected_at", since)
        .order("published_at", desc=True)
        .limit(config.SELECTION_POOL_PER_CATEGORY)
        .execute()
    )
    return response.data or []


def recent_titles(category: str) -> list[str]:
    response = (
        get_client().table("cards")
        .select("final_title")
        .eq("category", category)
        .order("created_at", desc=True)
        .limit(config.RECENT_TOPIC_LOOKBACK)
        .execute()
    )
    return [row["final_title"] for row in (response.data or []) if row.get("final_title")]


def is_recent_duplicate(item: dict, prior_titles: list[str]) -> bool:
    return any(
        fuzz.token_sort_ratio(item["title"], title) >= config.RECENT_TOPIC_SIMILARITY_THRESHOLD
        for title in prior_titles
    )


def choose_with_editorial_model(category: str, candidates: list[dict]) -> int | None:
    """조회수 데이터가 없으므로 영향도·시의성·설명 가능성으로 편집 판단을 한다."""
    choices = [
        {"id": item["id"], "title": item["title"], "source": item.get("source"), "summary": item.get("summary")}
        for item in candidates
    ]
    prompt = (
        "너는 한국 경제 카드뉴스의 편집장이다. 아래 후보 중 오늘 독자에게 가장 중요하고 "
        "관심을 받을 가능성이 높으며, 사실 기반으로 쉽게 설명할 수 있는 기사 딱 1건을 골라라. "
        "단순 기업 홍보, 제목만 자극적인 기사, 이미 비슷한 주제는 제외한다. JSON만 반환: {\"id\": 정수}.\n\n"
        f"카테고리: {config.CATEGORY_LABELS.get(category, category)}\n후보: {json.dumps(choices, ensure_ascii=False)}"
    )
    raw = call_text_model("반드시 유효한 JSON 한 객체만 반환한다.", prompt, max_tokens=80).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    picked = json.loads(raw)
    candidate_ids = {item["id"] for item in candidates}
    return picked["id"] if picked.get("id") in candidate_ids else None


def select_category(category: str) -> dict | None:
    history = recent_titles(category)
    candidates = [item for item in fetch_candidates(category) if not is_recent_duplicate(item, history)]
    if not candidates:
        log.info("[%s] 새롭고 적합한 후보 없음", category)
        events.record("select", "skipped", category=category, count=0, message="새롭고 적합한 후보 없음")
        return None
    try:
        selected_id = choose_with_editorial_model(category, candidates)
    except Exception as exc:
        log.warning("[%s] 편집 선택 실패, 최신 기사로 대체: %s", category, exc)
        selected_id = None
    selected = next((item for item in candidates if item["id"] == selected_id), candidates[0])
    get_client().table("news_items").update({"status": "approved"}).eq("id", selected["id"]).execute()
    log.info("[%s] 자동 선정: %s", category, selected["title"])
    events.record(
        "select", "success", category=category, count=len(candidates), news_item_id=selected["id"],
        message=selected["title"], detail={"pool": len(candidates), "editorial_pick": selected_id is not None},
    )
    return selected


def main() -> None:
    for category in config.CATEGORIES:
        select_category(category)


if __name__ == "__main__":
    main()

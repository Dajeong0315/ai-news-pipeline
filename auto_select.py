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
ALLOW_EMPTY_PICK = config.CHANNEL_CONFIG.get("allow_empty_pick", False)  # 하루 0건을 허용하는 채널


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
        f"너는 {config.CHANNEL_CONFIG['selector_role']}이다. 아래 후보 중 오늘 독자에게 가장 중요하고 "
        "관심을 받을 가능성이 높으며, 사실 기반으로 쉽게 설명할 수 있는 기사 딱 1건을 골라라. "
        "단순 기업 홍보, 제목만 자극적인 기사, 이미 비슷한 주제는 제외한다. "
        f"{extra + ' ' if (extra := config.CHANNEL_CONFIG.get('selector_extra')) else ''}JSON만 반환: {{\"id\": 정수}}.\n\n"
        f"카테고리: {config.CATEGORY_LABELS.get(category, category)}\n후보: {json.dumps(choices, ensure_ascii=False)}"
    )
    raw = call_text_model("반드시 유효한 JSON 한 객체만 반환한다.", prompt, max_tokens=80).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    picked = json.loads(raw)
    candidate_ids = {item["id"] for item in candidates}
    return picked["id"] if picked.get("id") in candidate_ids else None


def eligible_candidates(category: str) -> list[dict]:
    history = recent_titles(category)
    # 채널이 정한 제목 제외어(예: 청년 채널 대외활동의 "수상", "성료" — 이미 끝난 대회는 모집 정보가 아니다)
    excluded = config.CHANNEL_CONFIG.get("exclude_title_words", {}).get(category, [])
    return [
        item for item in fetch_candidates(category)
        if not is_recent_duplicate(item, history) and not any(word in item["title"] for word in excluded)
    ]


def select_category(category: str) -> dict | None:
    candidates = eligible_candidates(category)
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


def choose_across_categories(candidates: list[dict], limit: int) -> list[int]:
    """카테고리를 섞은 후보에서 1~limit건. 좋은 기사가 하나뿐이면 1건만 고르게 한다."""
    choices = [
        {"id": item["id"], "category": config.CATEGORY_LABELS.get(item["category"]), "title": item["title"],
         "summary": (item.get("summary") or "")[:200]}
        for item in candidates
    ]
    # 그날 0건을 허용하는 채널(청년, 2026-10-30): 지역 한정 행사뿐인 날 억지로 1건을 채우지 않는다.
    minimum = 0 if ALLOW_EMPTY_PICK else 1
    empty_note = "올릴 만한 기사가 없으면 {\"ids\": []}로 비워 둔다. " if ALLOW_EMPTY_PICK else ""
    prompt = (
        f"너는 {config.CHANNEL_CONFIG['selector_role']}이다. 아래 후보 중 오늘 올릴 기사를 {minimum}~{limit}건 골라라. "
        f"정말 좋은 기사가 하나뿐이면 1건만 고른다. 여러 건이면 서로 다른 주제로 고른다. {empty_note}"
        "단순 기업 홍보, 제목만 자극적인 기사는 제외한다. "
        f"{config.CHANNEL_CONFIG.get('selector_extra', '')} JSON만 반환: {{\"ids\": [정수, ...]}}.\n\n"
        f"후보: {json.dumps(choices, ensure_ascii=False)}"
    )
    raw = call_text_model("반드시 유효한 JSON 한 객체만 반환한다.", prompt, max_tokens=120).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidate_ids = {item["id"] for item in candidates}
    return [i for i in json.loads(raw).get("ids", []) if i in candidate_ids][:limit]


def select_daily(limit: int) -> list[dict]:
    """하루 게시 수를 정한 채널(청년, 하루 1~2개): 카테고리별 1건씩이 아니라 전체 후보에서 고른다."""
    candidates = [item for category in config.CATEGORIES for item in eligible_candidates(category)]
    if not candidates:
        log.info("새롭고 적합한 후보 없음")
        events.record("select", "skipped", count=0, message="새롭고 적합한 후보 없음")
        return []
    try:
        picked_ids = choose_across_categories(candidates, limit)
    except Exception as exc:
        log.warning("편집 선택 실패%s: %s", "" if ALLOW_EMPTY_PICK else ", 최신 기사 1건으로 대체", exc)
        picked_ids = []
    # 0건 허용 채널은 최신 기사로 채우지 않는다(지역 한정 행사가 그대로 올라가는 걸 막으려고)
    selected = [item for item in candidates if item["id"] in picked_ids] or ([] if ALLOW_EMPTY_PICK else candidates[:1])
    if not selected:
        log.info("편집장이 고른 기사 없음(0건 허용 채널)")
        events.record("select", "skipped", count=len(candidates), message="올릴 만한 기사 없음(0건)")
    for item in selected:
        get_client().table("news_items").update({"status": "approved"}).eq("id", item["id"]).execute()
        log.info("[%s] 자동 선정: %s", item["category"], item["title"])
        events.record(
            "select", "success", category=item["category"], count=len(candidates), news_item_id=item["id"],
            message=item["title"], detail={"pool": len(candidates), "editorial_pick": bool(picked_ids), "daily_limit": limit},
        )
    return selected


def main() -> None:
    daily_posts = config.CHANNEL_CONFIG.get("daily_posts")
    if daily_posts:
        select_daily(daily_posts)
        return
    for category in config.CATEGORIES:
        select_category(category)


if __name__ == "__main__":
    main()

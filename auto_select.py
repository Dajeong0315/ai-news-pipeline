"""카테고리별로 오늘의 기사 한 건을 자동 선정해 승인 상태로 전환한다."""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from rapidfuzz import fuzz

import config
import events
from db import get_client
from llm_client import call_text_model

log = logging.getLogger("auto_select")
ALLOW_EMPTY_PICK = config.CHANNEL_CONFIG.get("allow_empty_pick", False)  # 하루 0건을 허용하는 채널
LISTINGS = config.CHANNEL_CONFIG.get("source") == "listings"  # 기사 대신 모집 공고를 쓰는 채널(디징, 2026-10-31)
KST = timezone(timedelta(hours=9))


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


# --- 모집 공고 선정(디징, 2026-10-31) ---
# 후보는 날짜로 먼저 거른다: 접수 중이고 마감이 오늘+3일 이상. 모델에게는 그 안에서 우선순위 점수 상위만 보여준다.
LISTING_MIN_DAYS_LEFT = 3
LISTING_POOL = 25
LISTING_RECENT_DAYS = 30
LISTING_DUP_THRESHOLD = 90  # 기관만 다른 "체험형 청년인턴 채용" 제목끼리는 78점을 넘어서 기사보다 높게 둔다
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만)\s*원")
_AMOUNT_UNIT = {"억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4}
LISTING_FIELDS = "id,title,source,url,category,keyword,summary,apply_start,apply_end,official_url,listing,status,published_at"


def listing_days_left(item: dict, today: date) -> int:
    return (date.fromisoformat(item["apply_end"]) - today).days


def _benefit_won(text: str) -> int:
    return max((int(float(n.replace(",", "")) * _AMOUNT_UNIT[u]) for n, u in _AMOUNT_RE.findall(text or "")), default=0)


def listing_priority(item: dict, today: date) -> int:
    """전국 > 광역, 마감 D-3~D-10이거나 오늘 접수 시작, 혜택 규모. 2030 체감도는 모델이 본다."""
    info = item.get("listing") or {}
    score = 30 if info.get("region") == "전국" else 10
    if listing_days_left(item, today) <= 10:
        score += 20
    if item.get("apply_start") == today.isoformat():
        score += 15
    if info.get("year_round"):  # 1년 내내 받는 사업은 "지금 챙길" 소식이 아니다
        score -= 25
    won = _benefit_won(f"{info.get('benefit') or ''} {info.get('raw_text') or ''}")
    score += 15 if won >= 10**7 else 10 if won >= 10**6 else 5 if won else 0
    if (info.get("headcount") or 0) >= 10:
        score += 10
    if info.get("kind") == "청년인턴":
        score += 5
    return score


def recent_listing_keys(since: datetime) -> tuple[set[str], list[str]]:
    """최근 30일 카드로 만든 공고의 (url·source_id, 제목). 같은 공고를 다른 출처에서 다시 고르지 않게 한다."""
    cards = (get_client().table("cards").select("news_item_id").in_("category", list(config.CATEGORIES))
             .gte("created_at", since.isoformat()).execute().data or [])
    ids = [c["news_item_id"] for c in cards if c.get("news_item_id")]
    if not ids:
        return set(), []
    items = get_client().table("news_items").select("title,url,listing").in_("id", ids).execute().data or []
    keys = ({i["url"] for i in items} | {(i.get("listing") or {}).get("source_id") for i in items}) - {None}
    return keys, [i["title"] for i in items]


def approved_listings_without_card(today: date | None = None) -> list[dict]:
    """선정됐지만 카드가 아직 없는 공고(한도 소진 등으로 미뤄진 것). 카드 생성과 다음 날 선정 수 계산이 같이 쓴다."""
    today = today or datetime.now(KST).date()
    approved = (get_client().table("news_items").select(LISTING_FIELDS)
                .eq("channel", config.CHANNEL).eq("source_type", "listing").eq("status", "approved")
                .gte("apply_end", (today + timedelta(days=LISTING_MIN_DAYS_LEFT)).isoformat()).execute().data or [])
    if not approved:
        return []
    done = get_client().table("cards").select("news_item_id").in_("news_item_id", [i["id"] for i in approved]).execute().data or []
    done_ids = {row["news_item_id"] for row in done}
    return [i for i in approved if i["id"] not in done_ids]


def listing_candidates(today: date) -> list[dict]:
    rows = (get_client().table("news_items").select(LISTING_FIELDS)
            .eq("channel", config.CHANNEL).eq("source_type", "listing").eq("status", "filtered")
            .gte("apply_end", (today + timedelta(days=LISTING_MIN_DAYS_LEFT)).isoformat())
            .or_(f"apply_start.is.null,apply_start.lte.{today.isoformat()}")  # 접수 중만(접수 예정은 시작일에 다시 본다)
            .limit(2000).execute().data or [])
    keys, titles = recent_listing_keys(datetime.now(timezone.utc) - timedelta(days=LISTING_RECENT_DAYS))
    kept: list[dict] = []
    for item in sorted(rows, key=lambda i: listing_priority(i, today), reverse=True):
        if item["url"] in keys or (item.get("listing") or {}).get("source_id") in keys:
            continue
        if any(fuzz.token_sort_ratio(item["title"], t) >= LISTING_DUP_THRESHOLD for t in titles + [k["title"] for k in kept]):
            continue
        kept.append(item)
    return kept[:LISTING_POOL]


def choose_listings(candidates: list[dict], limit: int, today: date) -> list[int]:
    def short(text, n):
        return str(text or "").replace("\n", " ")[:n] or None

    choices = []
    for item in candidates:
        info = item.get("listing") or {}
        choices.append({
            "id": item["id"], "분야": config.CATEGORY_LABELS.get(item["category"]), "제목": item["title"],
            "기관": item.get("source"), "지역": info.get("region"), "대상": short(info.get("target"), 120),
            "혜택": short(info.get("benefit"), 120), "접수 시작": item.get("apply_start"), "마감": item["apply_end"],
            "남은 날": f"D-{listing_days_left(item, today)}", "연중 사업": bool(info.get("year_round")),
        })
    prompt = (
        f"너는 {config.CHANNEL_CONFIG['selector_role']}이다. 아래는 오늘 접수 중인 공식 모집 공고다(마감이 모두 3일 이상 남음). "
        f"오늘 카드뉴스로 올릴 공고를 0~{limit}건 골라라. 기준(앞일수록 중요): "
        "1) 2030이 실제로 신청할 수 있고 체감이 큰 것(대학생·취준생·사회초년생이 바로 해당되는 것). "
        "특정 학교 재학생·장애인 전형·중장년·기업 전용처럼 대상이 좁으면 뒤로 미룬다. "
        "2) 전국 단위 > 광역 시도. 3) 마감이 D-3~D-10으로 가깝거나 오늘 접수를 시작한 것. 4) 혜택 규모(지원금·상금·채용 인원). "
        "연중 사업(1년 내내 받는 것)은 다른 후보가 없을 때만. 여러 건이면 서로 다른 분야로 고른다. "
        "올릴 만한 공고가 없으면 {\"ids\": []}로 비워 둔다. JSON만 반환: {\"ids\": [정수, ...]}.\n\n"
        f"오늘: {today.isoformat()}\n후보: {json.dumps(choices, ensure_ascii=False)}"
    )
    raw = call_text_model("반드시 유효한 JSON 한 객체만 반환한다.", prompt, max_tokens=120).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidate_ids = {item["id"] for item in candidates}
    return [i for i in json.loads(raw).get("ids", []) if i in candidate_ids][:limit]


def select_listings(limit: int) -> list[dict]:
    today = datetime.now(KST).date()
    pending = approved_listings_without_card(today)  # 전에 카드로 못 만든 선정분이 있으면 그만큼 덜 고른다
    limit = max(0, limit - len(pending))
    candidates = listing_candidates(today) if limit else []
    if not candidates:
        reason = f"카드 대기 중인 선정 공고 {len(pending)}건" if pending and not limit else "접수 중인 새 공고 없음"
        log.info(reason)
        events.record("select", "skipped", count=0, message=reason)
        return []
    try:
        picked_ids = choose_listings(candidates, limit, today)
    except Exception as exc:
        log.warning("편집 선택 실패(0건 처리): %s", exc)
        picked_ids = []
    selected = [item for item in candidates if item["id"] in picked_ids]
    if not selected:
        log.info("편집장이 고른 공고 없음")
        events.record("select", "skipped", count=len(candidates), message="올릴 만한 공고 없음(0건)")
    for item in selected:
        get_client().table("news_items").update({"status": "approved"}).eq("id", item["id"]).execute()
        log.info("[%s] 자동 선정: %s (마감 %s, D-%d, %s)", item["category"], item["title"], item["apply_end"],
                 listing_days_left(item, today), item.get("keyword"))
        events.record(
            "select", "success", category=item["category"], count=len(candidates), news_item_id=item["id"],
            message=item["title"], detail={"pool": len(candidates), "daily_limit": limit, "source": item.get("keyword"),
                                           "apply_end": item["apply_end"], "priority": listing_priority(item, today)},
        )
    return selected


def main() -> None:
    daily_posts = config.CHANNEL_CONFIG.get("daily_posts")
    if LISTINGS:
        select_listings(daily_posts or 2)
        return
    if daily_posts:
        select_daily(daily_posts)
        return
    for category in config.CATEGORIES:
        select_category(category)


if __name__ == "__main__":
    main()

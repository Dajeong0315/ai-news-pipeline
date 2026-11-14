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
# 분야별로 선정 모델에 보여줄 최신 기사 수(경제 40, 2026-11-02 — 20건 상한에 11/2 금리 분야 32건 중 12건이 아예 안 보였다)
SELECTION_POOL = int(config.CHANNEL_CONFIG.get("selection_pool", config.SELECTION_POOL_PER_CATEGORY))
# 순위 후보(2026-11-02): 1등이 카드 작성 단계에서 떨어지면 2등·3등… 순서로 시도한다. 하루 시도 수는 max_daily_attempts.
RANK_SIZE = int(config.CHANNEL_CONFIG.get("rank_size", 1))
HOLD_TOPICS = tuple(config.CHANNEL_CONFIG.get("hold_topics", ()))  # 제목에 있으면 보류(경제: 청년미래적금, 디징 콜라보 전까지)


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
        .limit(SELECTION_POOL)
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


# --- 중복 게시 차단(2026-10-31) ---
# 10/2에 "코스피 소폭 하락" 3개, "중국-미국 협의" 2개처럼 같은 기사가 카테고리만 달리해 여러 번 올라갔다. 위 검사는
# 15자로 잘린 cards.final_title만 봐서 놓쳤다. 최근 7일 카드(게시·미게시 모두)의 기사 원문 제목·URL과 비교한다.
DUP_LOOKBACK_DAYS = 7
DUP_TITLE_THRESHOLD = 80  # 정규화한 제목 유사도(0~100)
_KEY_NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%p|%|bp|조|억|만|천|원|배|명|건|달러|포인트|선)?")
_TOPIC_STOPWORDS = {"오늘", "올해", "내년", "지난해", "전년", "대비", "기록", "발표", "전망", "이유", "정부", "한국", "국내"}


def _key_numbers(title: str) -> set[str]:
    """단위가 붙은 수치, 또는 세 자리 이상 수(연도 제외). "2,550선"과 "2,550"은 같은 수로 본다."""
    found = set()
    for number, unit in _KEY_NUMBER_RE.findall(title):
        digits = number.replace(",", "")
        if unit or (len(digits.split(".")[0]) >= 3 and not re.fullmatch(r"(19|20)\d\d", digits)):
            found.add(digits)
    return found


def _topic_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z]{2,}", title) if w not in _TOPIC_STOPWORDS}


def duplicate_reason(item: dict, history: list[dict]) -> str | None:
    """같은 기사 URL, 정규화 제목 유사도 80 이상, 같은 핵심 수치 + 같은 주제어면 중복. 걸린 이유를 돌려준다."""
    import slide_writer  # slide_writer가 config를 읽어 채널 설정을 잡으므로 함수 안에서 불러 순환 import를 피한다

    title = slide_writer.normalize_title(item["title"])
    numbers, words = _key_numbers(slide_writer.strip_outlet(item["title"])), _topic_words(title)
    for prev in history:
        if prev.get("id") == item.get("id"):
            continue
        if item.get("url") and item["url"] == prev.get("url"):
            return f"같은 기사 URL(이전: {prev['title'][:30]})"
        other = slide_writer.normalize_title(prev["title"])
        score = fuzz.token_sort_ratio(title, other)
        if score >= DUP_TITLE_THRESHOLD:
            return f"제목 유사도 {score:.0f}(이전: {prev['title'][:30]})"
        shared = numbers & _key_numbers(slide_writer.strip_outlet(prev["title"]))
        if shared and words & _topic_words(other):
            return f"같은 핵심 수치 {sorted(shared)}·주제어 {sorted(words & _topic_words(other))[:3]}(이전: {prev['title'][:30]})"
    return None


def recent_posted_items(days: int = DUP_LOOKBACK_DAYS) -> list[dict]:
    """최근 days일 이 채널 카드의 원본 기사(id·title·url). 인스타에서 지운 게시물도 기록이 남아 있어 같이 막는다."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cards = (get_client().table("cards").select("news_item_id").in_("category", list(config.CATEGORIES))
             .gte("created_at", since).execute().data or [])
    ids = [c["news_item_id"] for c in cards if c.get("news_item_id")]
    if not ids:
        return []
    return get_client().table("news_items").select("id,title,url").in_("id", ids).execute().data or []


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


def eligible_candidates(category: str, posted: list[dict] | None = None, rejected: list | None = None) -> list[dict]:
    """rejected를 넘기면 빠진 후보를 (기사, 이유)로 채운다(dry-run·진단용)."""
    from collect_news import foreign_reason  # collect_news가 config를 읽으므로 함수 안에서 불러 순환 import를 피한다

    def drop(item: dict, reason: str) -> None:
        log.info("[%s] %s: %s", category, reason, item["title"])
        if rejected is not None:
            rejected.append((item, reason))

    history = recent_titles(category)
    # 채널이 정한 제목 제외어(예: 청년 채널 대외활동의 "수상", "성료" — 이미 끝난 대회는 모집 정보가 아니다)
    excluded = config.CHANNEL_CONFIG.get("exclude_title_words", {}).get(category, [])
    posted = recent_posted_items() if posted is None else posted
    kept = []
    for item in fetch_candidates(category):
        if is_recent_duplicate(item, history):
            drop(item, "최근 카드 제목과 비슷함")
            continue
        if (word := next((w for w in excluded if w in item["title"]), None)):
            drop(item, f"제목 제외어({word})")
            continue
        if (reason := foreign_reason(item["title"], item.get("source"))):
            drop(item, f"해외 매체 제외({reason})")
            continue
        if (word := next((w for w in HOLD_TOPICS if w in item["title"]), None)):
            drop(item, f"보류 주제({word})")
            continue
        if (pattern := excluded_topic(item["title"])):
            drop(item, f"주제 필터 제외({pattern})")
            continue
        if (reason := duplicate_reason(item, posted + kept)):  # 같은 실행 후보끼리도(카테고리만 다른 같은 기사)
            drop(item, f"중복 차단({reason})")
            continue
        kept.append(item)
    return kept


# 채널 주제 필터(경제 2026-10-31: 종목·시황·인사·행사·환율 트레이딩·정치 발언 제목). 정규식 목록, 하나라도 맞으면 후보에서 뺀다.
_EXCLUDE_TOPIC_RES = [re.compile(p) for p in config.CHANNEL_CONFIG.get("exclude_title_patterns", ())]


def excluded_topic(title: str) -> str | None:
    return next((r.pattern for r in _EXCLUDE_TOPIC_RES if r.search(title)), None)


def select_category(category: str, posted: list[dict] | None = None) -> dict | None:
    candidates = eligible_candidates(category, posted)
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


def _choice(item: dict) -> dict:
    """선정 모델에 보여줄 후보. 구글 뉴스 요약이 "제목&nbsp;&nbsp;매체명"뿐이면 요약은 빼서 토큰을 아낀다."""
    import slide_writer  # slide_writer가 config를 읽어 채널 설정을 잡으므로 함수 안에서 불러 순환 import를 피한다

    title = slide_writer.strip_outlet(item["title"])
    summary = (item.get("summary") or "").replace("&nbsp;", " ").strip()
    choice = {"id": item["id"], "category": config.CATEGORY_LABELS.get(item["category"]), "title": title}
    if summary and not summary.startswith(title[:20]):
        choice["summary"] = summary[:200]
    return choice


def rank_across_categories(candidates: list[dict], size: int) -> list[int]:
    """카테고리를 섞은 후보에 순위를 매겨 최대 size건(2026-11-02). 1등이 카드 작성에서 떨어지면 다음 순위를 쓴다."""
    choices = [_choice(item) for item in candidates]
    prompt = (
        f"너는 {config.CHANNEL_CONFIG['selector_role']}이다. 아래 후보 중 오늘 카드뉴스로 만들 기사를 좋은 순서대로 "
        f"최대 {size}건 골라 순위를 매겨라. 1등이 쓸 수 없게 되면 2등을 쓰므로 서로 다른 주제로 고른다. "
        "단순 기업 홍보, 제목만 자극적인 기사는 제외한다. "
        f"{config.CHANNEL_CONFIG.get('selector_extra', '')} "
        "JSON만 반환: {\"ids\": [1등 id, 2등 id, ...]}.\n\n"
        f"후보: {json.dumps(choices, ensure_ascii=False)}"
    )
    raw = call_text_model("반드시 유효한 JSON 한 객체만 반환한다.", prompt, max_tokens=160).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidate_ids = {item["id"] for item in candidates}
    ranked: list[int] = []
    for i in json.loads(raw).get("ids", []):
        if i in candidate_ids and i not in ranked:
            ranked.append(i)
    return ranked[:size]


def choose_across_categories(candidates: list[dict], limit: int) -> list[int]:
    """카테고리를 섞은 후보에서 1~limit건. 좋은 기사가 하나뿐이면 1건만 고르게 한다."""
    choices = [_choice(item) for item in candidates]
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


def daily_candidates(rejected: list | None = None) -> list[dict]:
    posted = recent_posted_items()
    candidates: list[dict] = []
    for category in config.CATEGORIES:  # 앞 카테고리 후보와도 비교해 같은 기사가 두 카테고리에서 함께 올라오지 않게
        candidates += eligible_candidates(category, posted + candidates, rejected)
    return candidates


def select_daily(limit: int) -> list[dict]:
    """하루 게시 수를 정한 채널(경제 하루 1개): 카테고리별 1건씩이 아니라 전체 후보에서 고른다.
    rank_size가 있으면(경제 5) 순위를 매겨 앞에서 limit건만 선정하고, 나머지 순위는 이벤트 detail.ranking에 남겨
    카드 작성 단계(generate_cardset)가 1등이 떨어졌을 때 다음 순위를 쓰게 한다."""
    candidates = daily_candidates()
    if not candidates:
        log.info("새롭고 적합한 후보 없음")
        events.record("select", "skipped", count=0, message="새롭고 적합한 후보 없음")
        return []
    ranking: list[int] = []
    try:
        if RANK_SIZE > 1:
            ranking = rank_across_categories(candidates, max(RANK_SIZE, limit))
            picked_ids = ranking[:limit]
        else:
            picked_ids = choose_across_categories(candidates, limit)
    except Exception as exc:
        log.warning("편집 선택 실패%s: %s", "" if ALLOW_EMPTY_PICK else ", 최신 기사 1건으로 대체", exc)
        picked_ids = []
    if not ranking and RANK_SIZE > 1 and not ALLOW_EMPTY_PICK:  # 모델 실패 시 최신순을 순위로 쓴다
        ranking = [item["id"] for item in candidates[:RANK_SIZE]]
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
            message=item["title"], detail={"pool": len(candidates), "editorial_pick": bool(picked_ids), "daily_limit": limit,
                                           "ranking": ranking},
        )
    titles = {item["id"]: item["title"] for item in candidates}
    for rank, item_id in enumerate(ranking, 1):
        log.info("순위 %d: %s", rank, titles.get(item_id))
    return selected


def todays_ranking() -> list[int]:
    """오늘 선정 이벤트에 남긴 순위(경제). 여러 번 선정했으면 마지막 것."""
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = (get_client().table("pipeline_events").select("detail")
            .eq("channel", config.CHANNEL).eq("stage", "select").eq("status", "success")
            .gte("created_at", start.isoformat()).order("created_at", desc=True).limit(5).execute().data or [])
    return next(((r.get("detail") or {}).get("ranking") for r in rows if (r.get("detail") or {}).get("ranking")), [])


def next_ranked(tried: set[int], ranking: list[int] | None = None) -> dict | None:
    """오늘 순위에서 아직 시도하지 않은 다음 기사를 선정 상태로 바꿔 돌려준다. 이미 빠졌거나(rejected) 카드가 된 기사는 건너뛴다."""
    ranking = todays_ranking() if ranking is None else ranking
    for rank, item_id in enumerate(ranking, 1):
        if item_id in tried:
            continue
        rows = (get_client().table("news_items").select("id,title,url,source,category,published_at,status")
                .eq("id", item_id).execute().data or [])
        if not rows or rows[0]["status"] not in ("filtered", "approved"):
            continue
        get_client().table("news_items").update({"status": "approved"}).eq("id", item_id).execute()
        log.info("[%s] 다음 순위(%d위) 선정: %s", rows[0]["category"], rank, rows[0]["title"])
        events.record("select", "retry", category=rows[0]["category"], news_item_id=item_id, message=rows[0]["title"],
                      detail={"rank": rank})
        return rows[0]
    return None


# --- 모집 공고 선정(디징, 2026-10-31) ---
# 후보는 날짜로 먼저 거른다: 접수 중이고 마감이 오늘+3일 이상. 모델에게는 그 안에서 우선순위 점수 상위만 보여준다.
LISTING_MIN_DAYS_LEFT = 3
LISTING_POOL = 25
LISTING_RECENT_DAYS = 30
LISTING_DUP_THRESHOLD = 90  # 기관만 다른 "체험형 청년인턴 채용" 제목끼리는 78점을 넘어서 기사보다 높게 둔다
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만)\s*원")
_AMOUNT_UNIT = {"억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4}
# source_type은 generate_cardset이 공고 경로(기사 fetch 안 함·apply_end 마감·공고 캡션 검사)를 고르는 기준이라 꼭 같이 읽는다.
# 2026-11-01 첫 실행에서 빠져 있어 공고가 기사로 처리됐다(잡알리오 페이지 fetch, D-n·기관명 검사 없음).
LISTING_FIELDS = ("id,title,source,source_type,url,category,keyword,summary,apply_start,apply_end,official_url,"
                  "listing,status,published_at")


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


def select_listings(limit: int, today: date | None = None) -> list[dict]:
    """today: 기준일(전날 제작은 내일 = 게시하는 날 기준으로 마감 3일 이상·접수 중)."""
    today = today or datetime.now(KST).date()
    pending = approved_listings_without_card(today)  # 전에 카드로 못 만든 선정분이 있으면 그만큼 덜 고른다
    limit = max(0, limit - len(pending))
    candidates = listing_candidates(today) if limit else []
    if not candidates:
        reason = (f"카드 대기 중인 선정 공고 {len(pending)}건" if pending and not limit
                  else "이번 실행·오늘 만들 카드 수를 다 채움" if not limit else "접수 중인 새 공고 없음")
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


def listing_run_limit(daily: int) -> int:
    """이번 실행에서 고를 공고 수. cards_per_run(디징 1)이 있으면 12:00·19:00 실행이 하나씩 만들고,
    오늘 이미 카드로 만든 공고(다시 만들기 포함 같은 공고는 1건)까지 합쳐 하루 daily_posts를 넘지 않게 한다(2026-11-02 승인 흐름)."""
    per_run = config.CHANNEL_CONFIG.get("cards_per_run")
    if not per_run:
        return daily
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = (get_client().table("cards").select("news_item_id").in_("category", list(config.CATEGORIES))
            .gte("created_at", start.isoformat()).execute().data or [])
    made = len({r["news_item_id"] for r in rows if r.get("news_item_id")})
    return max(0, min(int(per_run), daily - made))


def main(limit: int | None = None) -> None:
    """limit(전날 제작, run_daily nextday): 다음 날 빈 자리 수만큼 고른다(카드 대기 중인 선정분은 select_listings가 뺀다)."""
    daily_posts = config.CHANNEL_CONFIG.get("daily_posts")
    if limit is not None:
        if limit <= 0:
            events.record("select", "skipped", count=0, message="내일 자리·예비분이 이미 다 차 있음")
        elif LISTINGS:
            select_listings(limit, today=datetime.now(KST).date() + timedelta(days=1))
        else:
            select_daily(limit)
        return
    if LISTINGS:
        select_listings(listing_run_limit(daily_posts or 2))
        return
    if daily_posts:
        select_daily(daily_posts)
        return
    posted = recent_posted_items()
    for category in config.CATEGORIES:  # 이번 실행에서 고른 기사도 다음 카테고리의 중복 기준에 넣는다
        if (selected := select_category(category, posted)):
            posted.append(selected)


if __name__ == "__main__":
    main()

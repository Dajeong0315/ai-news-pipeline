"""디징(youth) 원본: 지금 신청할 수 있는 공식 모집 공고를 모아 news_items(source_type='listing')에 저장한다.

2026-10-31부터 디징은 뉴스 기사 대신 공고를 쓴다(기사는 대부분 사후 보도라 컨셉과 안 맞음, 10/30 한남대 박람회 건).
출처 네 곳을 공통 스키마로 정규화하고, 접수 기간이 남은 것만 남긴다.

- 잡알리오(공공기관 채용): 청년인턴(체험형·채용형)과 신입 정규·무기계약직 → youth_job
- 온통청년(청년정책): 신청 기간이 날짜로 정해진 전국·광역 정책. 시군구 단위·연령 미달 정책 제외 → youth_support
- K-Startup(창업 지원사업 공고): 공공기관·지자체가 예비창업자·청년을 받는 접수 중 공고 → youth_support
- Aside 루틴 inbox(channels/youth/inbox/aside-<오늘>.json, -test 파일 제외): 대외활동·공모전 → youth_activity

공통 스키마: title, host, target, benefit, apply_start, apply_end, region, official_url, listing_url, raw_text,
source(jobalio/youthcenter/kstartup/aside), source_id. DB 칸 쓰임은 sql/2026-10-31_listings.sql 참고.
마감이 없는 상시 공고는 카드에 D-n을 쓸 수 없어 모으지 않는다.

실행:
    CHANNEL=youth python collect_listings.py            # 수집·저장
    CHANNEL=youth python collect_listings.py --dry-run  # 저장 없이 건수만 보고 output/listings-<날짜>.json에 기록
"""

import html
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from rapidfuzz import fuzz

import config
import events

log = logging.getLogger("collect_listings")

KST = timezone(timedelta(hours=9))
MIN_DAYS_LEFT = 3  # 마감이 오늘+3일 이상
START_WITHIN_DAYS = 7  # 접수 예정이면 시작이 오늘+7일 이내
YEAR_ROUND_DAYS = 120  # 신청 기간이 이보다 길면 "연중" 사업으로 보고 선정 우선순위를 낮춘다
TITLE_DUP_THRESHOLD = 90
RAW_TEXT_MAX = 4000
INBOX_DIR = config.CHANNEL_DIR / "inbox"
SOURCE_LABELS = {"jobalio": "잡알리오", "youthcenter": "온통청년", "kstartup": "K-Startup", "aside": "Aside"}

JOBALIO_URL = "https://apis.data.go.kr/1051000/recruitment/list"
YOUTHCENTER_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"
KSTARTUP_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
PAGE_SIZE = 500
NATIONWIDE_REGIONS = 8  # 잡알리오 근무지가 이만큼 여러 시도에 걸치면 전국 채용으로 본다
MAX_PAGES = 20

# 온통청년 지역 판정. 등록 최상위 기관이 광역 시도면 그 지역, 중앙부처·정부산하기관이면 전국.
METRO_SHORT = {
    "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전", "울산": "울산",
    "세종": "세종", "경기": "경기", "강원": "강원", "충청북": "충북", "충청남": "충남", "전북": "전북", "전라북": "전북",
    "전남광주": "전남광주", "전라남": "전남", "경상북": "경북", "경상남": "경남", "제주": "제주",
}
_METRO_SUFFIX = ("특별시", "광역시", "특별자치시", "특별자치도", "통합특별시", "도")
_METRO_ABBR = {f"{name}시" for name in ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종")}
_LOCAL_TOKEN_RE = re.compile(r"^[가-힣]{1,5}(시|군|구)청?$")
_NOT_PLACE = {"도시", "전시", "상시", "수시", "즉시", "연구", "지구", "가구", "친구", "도구", "직군", "청년가구"}  # 시·군·구로 끝나는 보통 낱말


def today_kst() -> date:
    return datetime.now(KST).date()


def parse_ymd(value) -> date | None:
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    try:
        return datetime.strptime(digits, "%Y%m%d").date() if len(digits) == 8 else None
    except ValueError:
        return None


def in_window(start: date | None, end: date | None, today: date) -> bool:
    return end is not None and end >= today + timedelta(days=MIN_DAYS_LEFT) and (
        start is None or start <= today + timedelta(days=START_WITHIN_DAYS))


def labelled(*pairs: tuple[str, object]) -> str:
    """("응시 자격", 본문) 쌍을 "■ 응시 자격\n본문" 단락으로 잇는다. 빈 값은 뺀다. 원문은 요약하지 않는다."""
    parts = [f"■ {label}\n{str(text).strip()}" for label, text in pairs if text and str(text).strip()]
    return "\n\n".join(parts)[:RAW_TEXT_MAX]


def get_json(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, timeout=90)
    resp.raise_for_status()
    return resp.json()


def listing(source: str, source_id, category: str, **fields) -> dict:
    return {"source": source, "source_id": f"{source}:{source_id}", "category": category, **fields}


# --- 잡알리오 -------------------------------------------------------------

def fetch_jobalio(today: date) -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(JOBALIO_URL, {"serviceKey": config.DATA_GO_KR_SERVICE_KEY, "resultType": "json",
                                      "numOfRows": PAGE_SIZE, "pageNo": page, "ongoingYn": "Y"})
        batch = data.get("result") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    out = []
    for r in rows:
        hire = r.get("hireTypeLst") or ""
        intern = "R1060" in hire or "R1070" in hire  # 청년인턴(체험형·채용형)
        # 신입 정규·무기계약직. 대체인력·비정규직만 뽑는 신입 공고는 2030이 챙길 공고가 아니라 뺀다.
        newbie = r.get("recrutSe") == "R2010" and ("R1010" in hire or "R1030" in hire) and r.get("replmprYn") != "Y"
        start, end = parse_ymd(r.get("pbancBgngYmd")), parse_ymd(r.get("pbancEndYmd"))
        if not (intern or newbie) or not in_window(start, end, today):
            continue
        sn = r["recrutPblntSn"]
        out.append(listing(
            "jobalio", sn, "youth_job",
            kind="청년인턴" if intern else "신입 채용",
            title=r.get("recrutPbancTtl"),
            host=r.get("instNm"),
            target=f"{r.get('recrutSeNm')} · {r.get('hireTypeNmLst')}",
            benefit=f"{r.get('hireTypeNmLst')} {r.get('recrutNope')}명 채용" if r.get("recrutNope") else r.get("hireTypeNmLst"),
            apply_start=start, apply_end=end,
            region="전국" if len((r.get("workRgnNmLst") or "").split(",")) >= NATIONWIDE_REGIONS else r.get("workRgnNmLst"),
            official_url=r.get("srcUrl"),
            listing_url=f"https://job.alio.go.kr/recruitview.do?idx={sn}",
            headcount=r.get("recrutNope"),
            raw_text=labelled(
                ("채용 구분", f"{r.get('recrutSeNm')} / {r.get('hireTypeNmLst')} / 모집 {r.get('recrutNope')}명"),
                ("근무지", r.get("workRgnNmLst")), ("직무(NCS)", r.get("ncsCdNmLst")), ("학력", r.get("acbgCondNmLst")),
                ("응시 자격", r.get("aplyQlfcCn")), ("우대 사항", r.get("prefCn")),
                ("전형 절차·접수 방법", r.get("scrnprcdrMthdExpln")),
            ),
        ))
    return out


# --- 온통청년 -------------------------------------------------------------

def is_local(*names: str | None) -> bool:
    """기관명·제목에 "정읍시", "영암군", "관악구청" 같은 시군구 이름이 있으면 시군구 단위 사업으로 본다."""
    for token in re.split(r"[\s()「」『』\[\]·,]+", " ".join(n or "" for n in names)):
        if (_LOCAL_TOKEN_RE.match(token) and token.removesuffix("청") not in _METRO_ABBR and token not in _NOT_PLACE
                and not token.endswith(_METRO_SUFFIX[:-1])):
            return True
    return False


def youthcenter_region(p: dict) -> str | None:
    """None이면 수집하지 않는다(시군구 단위 또는 등록 기관을 알 수 없음)."""
    top = (p.get("rgtrHghrkInstCdNm") or "").strip()
    if not top or is_local(p.get("sprvsnInstCdNm"), p.get("rgtrInstCdNm"), p.get("plcyNm")):
        return None
    if top == "광주시청":
        return "광주"
    if top.endswith(_METRO_SUFFIX):
        return next((short for key, short in METRO_SHORT.items() if top.startswith(key)), top)
    return "전국"


def fetch_youthcenter(today: date) -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(YOUTHCENTER_URL, {"apiKeyNm": config.YOUTHCENTER_API_KEY, "pageNum": page,
                                          "pageSize": PAGE_SIZE, "rtnType": "json"})
        batch = (data.get("result") or {}).get("youthPolicyList") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    out = []
    for p in rows:
        # 신청기간 구분 0057001=특정기간, 0057002=상시, 0057003=마감. aplyYmd는 "YYYYMMDD ~ YYYYMMDD"(여러 줄이면 첫 줄)
        if p.get("aplyPrdSeCd") != "0057001" or "~" not in (p.get("aplyYmd") or ""):
            continue
        first, _, last = p["aplyYmd"].splitlines()[0].partition("~")
        start, end = parse_ymd(first), parse_ymd(last)
        if not in_window(start, end, today):
            continue
        min_age, max_age = int(p.get("sprtTrgtMinAge") or 0), int(p.get("sprtTrgtMaxAge") or 0)
        if (max_age and max_age < 19) or min_age > 34:  # 청소년 전용·중장년 전용
            continue
        region = youthcenter_region(p)
        if not region:
            continue
        age = f"만 {min_age}~{max_age}세" if max_age else (f"만 {min_age}세 이상" if min_age else None)
        target = " / ".join(x.strip() for x in (age, p.get("earnEtcCn"), p.get("addAplyQlfcCndCn")) if x and x.strip())
        out.append(listing(
            "youthcenter", p["plcyNo"], "youth_support",
            kind=p.get("mclsfNm") or p.get("lclsfNm"),
            title=p.get("plcyNm"),
            host=(p.get("sprvsnInstCdNm") or p.get("rgtrInstCdNm") or "").strip(),
            target=target or None,
            benefit=(p.get("plcySprtCn") or "").strip() or None,
            apply_start=start, apply_end=end,
            region=region,
            official_url=(p.get("aplyUrlAddr") or p.get("refUrlAddr1") or "").strip() or None,
            listing_url=f"https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/{p['plcyNo']}",
            year_round=(end - start).days > YEAR_ROUND_DAYS if start else False,
            raw_text=labelled(
                ("정책 설명", p.get("plcyExplnCn")), ("지원 내용", p.get("plcySprtCn")),
                ("신청 기간", p.get("aplyYmd")), ("사업 기간", p.get("bizPrdEtcCn")),
                ("지원 대상", target), ("참여 제한 대상", p.get("ptcpPrpTrgtCn")),
                ("신청 방법", p.get("plcyAplyMthdCn")), ("심사 방법", p.get("srngMthdCn")),
                ("제출 서류", p.get("sbmsnDcmntCn")), ("기타", p.get("etcMttrCn")),
                ("주관 기관", p.get("sprvsnInstCdNm")), ("운영 기관", p.get("operInstCdNm")),
            ),
        ))
    return out


# --- K-Startup ------------------------------------------------------------

def _url(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    return value if value.startswith("http") else f"https://{value}"


def fetch_kstartup(today: date) -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(KSTARTUP_URL, {"serviceKey": config.DATA_GO_KR_SERVICE_KEY, "page": page, "perPage": PAGE_SIZE,
                                       "returnType": "json", "cond[rcrt_prgs_yn::EQ]": "Y"})
        batch = data.get("data") or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    out = []
    for r in rows:
        r = {k: html.unescape(v) if isinstance(v, str) else v for k, v in r.items()}
        title = r.get("biz_pbanc_nm") or ""
        # 민간 액셀러레이터 투자 유치·대학 창업지원단(재학생 전용이 많음) 공고는 빼고 공공기관·지자체만
        if r.get("sprv_inst") not in ("공공기관", "지자체"):
            continue
        if "예비창업자" not in (r.get("biz_enyy") or "") and "청년" not in title:
            continue
        # 특강·포럼·상담회 같은 행사 참가 모집은 지원사업이 아니다. 구청·시청 단위 프로그램도 뺀다(supt_regin은 광역까지만 있음)
        if r.get("supt_biz_clsfc") == "행사ㆍ네트워크" or is_local(r.get("pbanc_ntrp_nm"), title):
            continue
        if "만 20세 이상 ~ 만 39세 이하" not in (r.get("biz_trgt_age") or ""):
            continue
        if not any(t in (r.get("aply_trgt") or "") for t in ("대학생", "일반인")):
            continue
        start, end = parse_ymd(r.get("pbanc_rcpt_bgng_dt")), parse_ymd(r.get("pbanc_rcpt_end_dt"))
        if not in_window(start, end, today):
            continue
        how = [f"{label}: {r[key]}" for key, label in (
            ("aply_mthd_onli_rcpt_istc", "온라인"), ("aply_mthd_eml_rcpt_istc", "이메일"), ("aply_mthd_vst_rcpt_istc", "방문"),
            ("aply_mthd_pssr_rcpt_istc", "우편"), ("aply_mthd_fax_rcpt_istc", "팩스"), ("aply_mthd_etc_istc", "기타"),
        ) if r.get(key)]
        out.append(listing(
            "kstartup", r["pbanc_sn"], "youth_support",
            kind=r.get("supt_biz_clsfc"),
            title=title.strip(),
            host=r.get("pbanc_ntrp_nm"),
            target=r.get("aply_trgt_ctnt") or r.get("aply_trgt"),
            benefit=f"{r.get('supt_biz_clsfc')} 지원" if r.get("supt_biz_clsfc") else None,
            apply_start=start, apply_end=end,
            region=r.get("supt_regin"),
            official_url=_url(r.get("biz_aply_url") or r.get("aply_mthd_onli_rcpt_istc") or r.get("biz_gdnc_url")),
            listing_url=r.get("detl_pg_url"),
            year_round=(end - start).days > YEAR_ROUND_DAYS if start else False,
            raw_text=labelled(
                ("공고 내용", r.get("pbanc_ctnt")), ("신청 대상", r.get("aply_trgt_ctnt") or r.get("aply_trgt")),
                ("대상 연령", r.get("biz_trgt_age")), ("창업 업력", r.get("biz_enyy")),
                ("제외 대상", r.get("aply_excl_trgt_ctnt")), ("우대 사항", r.get("prfn_matr")),
                ("지원 분야", r.get("supt_biz_clsfc")), ("지역", r.get("supt_regin")),
                ("접수 기간", f"{r.get('pbanc_rcpt_bgng_dt')} ~ {r.get('pbanc_rcpt_end_dt')}"),
                ("신청 방법", "\n".join(how)), ("주관 기관", f"{r.get('pbanc_ntrp_nm')} {r.get('biz_prch_dprt_nm') or ''}"),
            ),
        ))
    return out


# --- Aside inbox ----------------------------------------------------------

def fetch_aside(today: date) -> list[dict]:
    path = INBOX_DIR / f"aside-{today.isoformat()}.json"  # -test 파일은 이름이 달라 읽히지 않는다
    if not path.exists():
        log.warning("Aside inbox 파일 없음: %s", path.name)
        return []
    out = []
    for item in json.loads(path.read_text(encoding="utf-8")).get("items") or []:
        start, end = parse_ymd(item.get("apply_start")), parse_ymd(item.get("apply_end"))
        if not item.get("title") or not item.get("listing_url") or not in_window(start, end, today):
            continue
        out.append(listing(
            "aside", item.get("id") or item["listing_url"], item.get("category") or "youth_activity",
            kind=item.get("kind"),
            **{k: item.get(k) for k in ("title", "host", "target", "benefit", "region", "official_url", "listing_url")},
            apply_start=start, apply_end=end,
            raw_text=(item.get("raw_text") or "")[:RAW_TEXT_MAX],
            notes=(item.get("checks") or {}).get("notes"),
        ))
    return out


FETCHERS = {"jobalio": fetch_jobalio, "youthcenter": fetch_youthcenter, "kstartup": fetch_kstartup, "aside": fetch_aside}


# --- 정규화·중복 제거·저장 --------------------------------------------------

def dedupe(items: list[dict]) -> list[dict]:
    """같은 공고: source_id, URL(수집처·공식), 제목 유사도 순으로 본다. 먼저 들어온 쪽(FETCHERS 순서)을 남긴다."""
    kept: list[dict] = []
    seen_keys: set[str] = set()
    for item in items:
        keys = {item["source_id"], item["listing_url"]} | ({item["official_url"]} if item.get("official_url") else set())
        if keys & seen_keys or any(
            fuzz.token_sort_ratio(item["title"], other["title"]) >= TITLE_DUP_THRESHOLD for other in kept
        ):
            continue
        seen_keys |= keys
        kept.append(item)
    return kept


def to_row(item: dict, now: str) -> dict:
    summary = " · ".join(str(x).strip().replace("\n", " ")[:120] for x in (item.get("target"), item.get("benefit")) if x)
    extra = {k: v for k, v in item.items() if k not in (
        "title", "category", "apply_start", "apply_end", "official_url") and v is not None}
    return {
        "title": item["title"].strip(),
        "url": item["listing_url"],
        "source": item.get("host"),
        "channel": config.CHANNEL,
        "category": item["category"],
        "keyword": SOURCE_LABELS[item["source"]],
        "summary": summary or None,
        "published_at": datetime.combine(item["apply_start"], datetime.min.time(), KST).isoformat() if item.get("apply_start") else None,
        "collected_at": now,  # 마지막으로 공고 목록에서 본 시각. 매일 갱신돼 대시보드 오늘 탭에 보인다
        "source_type": "listing",
        "apply_start": item["apply_start"].isoformat() if item.get("apply_start") else None,
        "apply_end": item["apply_end"].isoformat(),
        "official_url": item.get("official_url"),
        "listing": extra,
    }


def save_rows(rows: list[dict]) -> tuple[int, int]:
    """새 공고는 status='filtered'로 넣고, 이미 있는 공고는 status를 빼고 내용·마감·collected_at만 갱신한다
    (선정·만료된 공고가 다시 후보로 돌아오지 않게). 반환: (새로 넣은 수, 갱신한 수)."""
    from db import get_client
    client = get_client()
    urls = [r["url"] for r in rows]
    existing: set[str] = set()
    for i in range(0, len(urls), 50):
        resp = client.table("news_items").select("url").eq("channel", config.CHANNEL).in_("url", urls[i:i + 50]).execute()
        existing |= {r["url"] for r in resp.data or []}
    new = [{**r, "status": "filtered"} for r in rows if r["url"] not in existing]
    old = [r for r in rows if r["url"] in existing]
    for i in range(0, len(new), 200):
        client.table("news_items").upsert(new[i:i + 200], on_conflict="channel,url", ignore_duplicates=True).execute()
    for i in range(0, len(old), 200):
        client.table("news_items").upsert(old[i:i + 200], on_conflict="channel,url").execute()
    return len(new), len(old)


def collect(today: date) -> tuple[list[dict], dict, dict]:
    items, counts, errors = [], {}, {}
    for source, fetch in FETCHERS.items():
        try:
            got = fetch(today)
        except Exception as e:
            log.error("[%s] 수집 실패: %s", source, config.redact(str(e)))
            errors[source] = config.redact(f"{type(e).__name__}: {e}")[:300]
            got = []
        counts[source] = len(got)
        log.info("[%s] 접수 기간 맞는 공고 %d건", source, len(got))
        items.extend(got)
    return dedupe(items), counts, errors


def main(dry_run: bool = False) -> None:
    today = today_kst()
    if not dry_run:
        events.record("collect", "started", message="공고 수집 시작(잡알리오·온통청년·K-Startup·Aside)")
    items, counts, errors = collect(today)
    by_source = {s: sum(1 for i in items if i["source"] == s) for s in FETCHERS}
    summary = ", ".join(f"{SOURCE_LABELS[s]} {by_source[s]}" for s in FETCHERS)
    log.info("중복 제거 후 %d건 (%s)", len(items), summary)
    now = datetime.now(timezone.utc).isoformat()
    rows = [to_row(item, now) for item in items]
    if dry_run:
        out = Path("output") / f"listings-{today.isoformat()}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("dry-run: 저장하지 않음, %s에 기록", out)
        return
    inserted, updated = save_rows(rows)
    log.info("신규 %d건, 갱신 %d건", inserted, updated)
    events.record(
        "collect", "success" if rows else "failed", count=len(rows),
        message=f"공고 {len(rows):,}건 ({summary}) · 신규 {inserted:,}건",
        detail={"fetched": counts, "kept": by_source, "inserted": inserted, "updated": updated, "errors": errors},
    )


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)

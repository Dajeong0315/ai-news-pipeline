"""승인된 뉴스 원문을 읽고 "묘한 경제" 브랜드의 4~6장 카드뉴스 content.json을 만든다.

기존 카드 1장(FLUX 배경+제목)짜리 파이프라인을 대체한다. 기사 원문을
스크래핑해 실제 사실을 근거로, 표지+용어 비유 설명+사실 근거(+의미)+고지
구조의 카드 세트를 LLM으로 생성하고, assets/templates/validate.js로 검증한다.

실행:
    python generate_cardset.py
"""

import json
import logging
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageStat
from rapidfuzz import fuzz

import config
import events
from db import get_client
from fetch_article import fetch_article_text
from generate_pose import generate_new_pose
from instagram_client import upload_to_storage
from llm_client import call_text_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("generate_cardset")

CHARACTER_DIR = Path("assets/character")
EXISTING_POSES = sorted(p.stem for p in CHARACTER_DIR.glob("*.png"))
ALLOWED_VISUALS = {"question", "hub", "bars", "timeline", "doc-up", "piggy-up", "pair", "flow", "alert"}
VISUAL_REQUIRED_FIELDS = {
    "hub": ["main", "a", "b"],
    "bars": ["items"],
    "timeline": ["points"],
    "flow": ["a", "b"],
}

SEASON_BY_MONTH = {
    3: "spring", 4: "spring", 5: "spring",
    6: "jangma", 7: "jangma",
    8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter", 1: "winter", 2: "winter",
}

MAX_ATTEMPTS = config.QA_MAX_ATTEMPTS

SYSTEM_PROMPT = """너는 인스타그램 경제 카드뉴스 계정 "묘한 경제"(@myohan.economy)의 콘텐츠 기획자이자,
집사에게 어려운 경제 용어를 비유로 풀어서 가르쳐주는 고양이 선생님이다.
어려운 경제 뉴스를 20대 사회초년생이 "아 그런 거였구나" 하고 이해하게 만드는 게 목표다.
단순히 기사 내용을 요약·재진술하는 게 아니라, 매번 실제로 경제 공부가 되도록 만든다.

## 절대 규칙
1. 카드 안의 모든 숫자·사실은 아래 [기사 원문]에 실제로 등장하는 내용만 사용한다. 원문에 없는 숫자를 지어내지 마라.
2. 클릭베이트 금지. 친근한 해요체.
3. 개인 맞춤 투자·재무 조언을 하지 않는다. 특정 종목·지역·자산을 사라고
   유도하는 문구를 어떤 카드에도 넣지 마라(action 카드도 마찬가지 —
   "오늘 관찰/검색/공부해볼 것" 같은 학습 행동만 제안하고, "OO에 투자하라"
   "OO 지역 부동산을 사라" 같은 매수 유도는 절대 금지).
4. 카드에는 출처 URL을 넣지 않는다.
5. 카드마다 서로 다른 역할·내용을 맡아야 한다. 같은 사실이나 같은 설명을
   두 장 이상에서 다른 말로 반복하지 마라(예: 2번 카드와 3번 카드가
   제목·본문이 사실상 같은 내용이면 안 됨). 아래 "카드 역할" 구조를 따르면
   자동으로 역할이 나뉜다.
6. body는 반드시 "…"로 끝나는 미완성 문장이 아니라, 완결된 문장(들)으로
   끝나야 한다. 110자 제한 안에 다 안 들어갈 것 같으면 처음부터 문장을
   짧게 나눠 써라(예: 긴 문장 하나 대신 짧은 문장 2개를 <br><br>로 연결).
   글자 수를 맞추려고 문장 중간을 자르지 마라. **한 문장은 되도록 40자를
   넘기지 마라** — 쉼표로 길게 이어 쓴 한 문장은 마침표 기준으로 안 잘려서
   전체가 통째로 잘려나갈 수 있다.

## 카드 역할 구조 (반드시 이 순서·역할로 구성)
1. **cover**: 후킹 한 줄. `stat`/`statArrow`는 표지에 128px 크기로 크게
   박히는 요소라, **등락률·금리·환율·주가·거래대금처럼 진짜 경제 지표일
   때만** 채워라(예: "2.75%", "-1.71%", "14개월"). "수상 16개 부문", "참가자
   500명"처럼 단순히 원문에 등장한 숫자일 뿐 그 자체로 경제 트렌드를
   보여주지 않는 카운트는 stat에 넣지 말고 비워둬라(hook 문장 안에 자연스럽게
   녹여 써도 된다). 이런 진짜 경제 지표가 원문에 아예 없으면 stat/statArrow
   필드를 통째로 생략해라.
2. **concept** (반드시 1장 포함): 이 기사에서 가장 어려운 용어나 개념 딱
   하나를 골라, **일상적인 비유**로 설명한다. title에는 그 용어를 그대로
   쓰고("기준금리 동결", "HBM이 뭐길래" 처럼), body는 반드시 장보기·택배·
   편의점·용돈·반려동물처럼 누구나 아는 일상 사물/행동에 빗대고, "~처럼",
   "~와 비슷해요", "~라고 생각하면 돼요" 같은 연결어를 최소 1번 써서
   설명한다. 사전적 정의를 그대로 나열하지 말 것.
3. **why**: 기사 원문에 나온 사실 근거로 "왜 이 일이 일어났는지"를 완결된
   문장으로 설명한다(단어·구절만 쉼표로 나열하지 말 것). concept 카드에서
   이미 한 비유 설명을 반복하지 말고, 사실관계만 다룬다.
4. **meaning** (선택, 카드를 5장 이상으로 구성할 때 사용): 이 트렌드가
   20대 사회초년생 독자의 일상이나 앞으로의 경제 공부에 왜 중요한지를
   완결된 문장으로 설명한다. concept/why와 다른 관점(영향·시사점)이어야 한다.
5. **action**: 오늘 해볼 학습·관찰 습관을 제안한다(투자 유도 금지, 규칙 3 참고).
6. **disclaimer**: 고정 문구(자동으로 채워짐).

## 비유 좋은 예 / 나쁜 예
- 나쁜 예(사전적 정의만 나열, 비유 없음): "HBM은 데이터를 빠르게 처리하는
  고대역폭 메모리 반도체입니다."
- 좋은 예(일상 비유 포함): {"type":"concept","num":"①","eyebrow":"오늘의 용어","title":"HBM이 뭐길래","body":"HBM은 반도체용 '급행 택배 전용도로'예요.<br><br>일반 도로 대신 전용도로를 깔면 택배가 훨씬 빨리 도착하듯, AI가 필요한 데이터를 더 빨리 받을 수 있어요."}
- 좋은 예 2: {"type":"concept","num":"①","eyebrow":"오늘의 용어","title":"기준금리 동결","body":"기준금리는 은행들의 '대출 도매가' 같은 거예요.<br><br>마트 도매가가 그대로면 손님 가격도 그대로이듯, 동결되면 대출·예금 이자도 당분간 크게 안 변해요."}

**주의**: 위 두 예시는 "비유를 이렇게 쓰라"는 형식 참고용일 뿐이다. 실제
출력에서는 절대 이 예시 문장을 그대로 베끼지 말고, 이번 기사의 실제 용어에
맞는 새로운 비유를 직접 만들어라(같은 용어가 다시 나와도 매번 다른 비유를
시도할 것 — 예: 택배·마트·용돈·버스·편의점 중 매번 다른 소재를 골라본다).

## 출력 형식 (JSON만 출력, 설명 텍스트 붙이지 마라)
{
  "cards": [
    {"type":"cover","badge":"짧고 후킹되는 배지 문구(이모지 1개 포함, 20자 이내)","hook":"핵심 한 줄(20자 이내)","stat":"등락률·금리·환율 등 진짜 경제 지표일 때만, 예: 2.75%(없으면 필드 자체를 생략)","statArrow":"↑ 또는 ↓(stat 있을 때만)","sub":"부제(선택, 20자 이내)","char":"포즈이름"},
    {"type":"concept","num":"①","eyebrow":"오늘의 용어","title":"용어 그대로(36자 이내)","body":"비유로 설명(110자 이내, 40자 안팎 문장을 <br><br>로 연결)","char":"포즈이름"},
    {"type":"why","eyebrow":"소제목(선택)","title":"카드 제목(36자 이내)","body":"원문 근거 사실(110자 이내)","char":"포즈이름","visual":{...}},
    ... (meaning 카드를 쓸 경우 여기 1장 추가) ...
    {"type":"action","eyebrow":"오늘 해볼 것 소개 문구","items":["학습·관찰 행동1","학습·관찰 행동2"],"char":"포즈이름"},
    {"type":"disclaimer","brand":"출처 & 안내","text":"이 콘텐츠는 교육·정보 제공용이며, 특정 투자 권유나 개인 맞춤 재무 조언이 아닙니다.","note":"자세한 출처는 캡션에서 확인하세요","handle":config.INSTAGRAM_HANDLE}
  ],
  "caption":"인스타그램 캡션(후킹 한 줄 + 본문 사실 요약 + 핵심 takeaway, 해시태그 최대 5개 포함)"
}

카드는 반드시 첫 번째가 cover, 마지막이 disclaimer여야 하고, concept 카드를
1장 이상 포함해 전체 4~6장이어야 한다(설명할 용어가 정말 없는 극히 단순한
기사일 때만 예외적으로 3장 허용). action 카드는 선택사항(있으면 disclaimer
바로 앞에 배치).

## 사용 가능한 고양이 포즈(char 필드, 아래 목록 중 하나를 최우선으로 사용)
__POSES__

포즈 선택 가이드: 부정적/리스크 내용→cat-worried, 판단이 필요한 내용→cat-thinking,
좋은 소식→cat-cheer, 저축·투자·소비→cat-money, 설명하는 카드→cat-explain,
놀람·반전→cat-surprised, 마무리·실천→cat-happy, 기본→cat-default.

위 목록 중 정말 어울리는 포즈가 하나도 없을 때만, "char" 대신
"char_new"에 필요한 포즈를 짧은 영어 설명으로 적어라(예:
"a cat happily raising both paws"). char와 char_new를 동시에 넣지
마라. 목록에 없는 이름을 char에 쓰지 마라. char_new 설명에는
초록색(green) 배경/소품을 절대 넣지 마라(투명 배경 처리에 초록색을
기준으로 쓰기 때문에 같이 지워짐) — 고양이의 포즈·표정만 묘사할 것.

## 사용 가능한 시각화 타입(visual 필드, 선택사항)
- {"type":"question"} : 물음표 강조
- {"type":"hub","main":"핵심어","a":"연결1","b":"연결2"}
- {"type":"bars","items":[{"h":30,"label":"라벨1"},{"h":32,"label":"라벨2","hi":true}]} (h는 상대적 크기 숫자, 최대 1장에만 사용)
- {"type":"timeline","points":[{"label":"값1"},{"label":"값2","now":true}]}
- {"type":"doc-up"} : 서류+상승 화살표
- {"type":"piggy-up"} : 저금통+상승 화살표
- {"type":"pair"} : 서류&저금통 나란히
- {"type":"flow","a":"항목1","b":"항목2"} : A→B 흐름
- {"type":"alert","label":"주의"} : 경고 아이콘

카테고리: __CATEGORY__
"""


def current_season() -> str:
    return SEASON_BY_MONTH[date.today().month]


def fetch_approved_without_cardset() -> list[dict]:
    client = get_client()
    approved_since = (datetime.now(timezone.utc) - timedelta(hours=config.MAX_AGE_HOURS)).isoformat()
    approved = (
        client.table("news_items")
        .select("id,title,url,category")
        .eq("status", "approved")
        .gte("collected_at", approved_since)
        .execute()
        .data
        or []
    )
    if not approved:
        return []
    existing = (
        client.table("cards")
        .select("news_item_id")
        .in_("news_item_id", [it["id"] for it in approved])
        .execute()
        .data
        or []
    )
    done_ids = {row["news_item_id"] for row in existing}
    return [it for it in approved if it["id"] not in done_ids]


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def _truncate(text: str, max_chars: int, *, strict: bool = False) -> str:
    """길이 제한을 넘으면 문장 단위로 끊어 마지막 완결 문장까지만 남긴다.

    예전에는 글자 수만 넘으면 무조건 단어 경계에서 자르고 말줄임표(…)를
    붙였는데, 그러면 "…영향을…"처럼 문장이 중간에 잘린 채로 카드에 그대로
    노출되는 문제가 실제로 발생했다(운영자 피드백, 2026-09-06). 완결된
    문장까지만 남기고 말줄임표는 붙이지 않는다.

    strict=True인데 문장 하나가 이미 제한보다 길어 완결 문장으로는 도저히
    못 자르는 경우, 조용히 단어 경계에서 잘라 보여주는 대신 예외를 던져
    LLM 재생성을 유도한다(본문 카드의 body 전용 — hook/title처럼 원래
    마침표 없는 짧은 문구는 strict를 쓰지 않는다)."""
    if text is None:
        return text
    plain = text.replace("<br><br>", " ").replace("<br>", " ").strip()
    if len(plain) <= max_chars:
        return plain

    kept = ""
    for sentence in _SENTENCE_END_RE.split(plain):
        candidate = f"{kept} {sentence}".strip() if kept else sentence
        if len(candidate) > max_chars:
            break
        kept = candidate
    if kept:
        return kept

    if strict:
        raise ValueError(f"문장 경계 안에서 {max_chars}자 제한을 만족하는 본문을 만들 수 없음: {plain[:40]}...")

    budget = max_chars
    cut = plain[:budget]
    last_space = cut.rfind(" ")
    if last_space > budget * 0.5:
        cut = cut[:last_space]
    return cut.rstrip(" ,.-·")


def sanitize_cards(data: dict) -> dict:
    """소형 LLM이 글자수 제한을 못 지키는 경우가 많아, 검증 전에 코드로 강제
    절삭하는 안전망(카드 제목 요약과 동일한 역할 분리 원칙: LLM=품질, 코드=제약)."""
    concept_num = 0
    for card in data.get("cards", []):
        if card.get("type") == "cover" and not card.get("badge"):
            card["badge"] = "오늘의 경제 🐾"
        if card.get("hook"):
            card["hook"] = _truncate(card["hook"], 20)
        if card.get("sub"):
            card["sub"] = _truncate(card["sub"], 20)
        if card.get("title"):
            card["title"] = _truncate(card["title"], 36)
        if card.get("body"):
            card["body"] = _truncate(card["body"], 110, strict=True)
        if card.get("stat"):
            # stat-num은 128px 폰트로 표지에 크게 박히는 요소라 아주 짧아야
            # 한다(예: "2.75%"). 한글이 섞이면 실제 렌더 폭이 넓어 카드
            # 가로 폭을 넘치는 경우가 있어 한글이 하나라도 섞이거나 6자를
            # 넘으면 통째로 제거한다(가로 방향 안전장치).
            #
            # 참고: stat-hero 자체의 세로 방향 "오버플로"는 한글 유무와
            # 무관하게 stat 필드가 있으면 거의 항상 발생하던 별개의 문제였고,
            # 원인은 이 값이 아니라 render.js의 오탐(폰트 메트릭 오차)이었음
            # — 2026-09-06 PROGRESS.md "남은 이슈" 참고, render.js에서
            # 수정함. 이 hangul 제거 로직은 그 문제와는 무관하지만 가로 폭
            # 안전장치로는 유효해 그대로 둔다.
            hangul_count = sum("가" <= ch <= "힣" for ch in card["stat"])
            if hangul_count >= 1 or len(card["stat"]) > 6:
                card.pop("stat", None)
                card.pop("statArrow", None)
            else:
                card["stat"] = card["stat"][:6]
        if card.get("char") not in EXISTING_POSES:
            char_new = card.pop("char_new", None)
            if char_new:
                try:
                    new_pose = generate_new_pose(char_new)
                    EXISTING_POSES.append(new_pose)  # 이번 배치의 다른 카드도 바로 재사용 가능
                    card["char"] = new_pose
                except Exception as e:
                    log.warning("새 포즈 생성 실패(%s), 기본 포즈로 대체: %s", char_new, e)
                    card["char"] = "cat-default"
            else:
                card["char"] = "cat-default"

        if card.get("type") == "disclaimer":
            # 고지문은 매번 LLM에게 그대로 재현해달라고 맡기지 않고 고정값으로 강제한다
            # (문구가 조금만 달라져도 validate.js가 정확 일치를 요구해 실패했었음).
            card["brand"] = "출처 & 안내"
            card["text"] = "이 콘텐츠는 교육·정보 제공용이며, 특정 투자 권유나 개인 맞춤 재무 조언이 아닙니다."
            card["note"] = "자세한 출처는 캡션에서 확인하세요"
            card["handle"] = config.INSTAGRAM_HANDLE

        visual = card.get("visual")
        if visual is not None:
            valid = (
                isinstance(visual, dict)
                and visual.get("type") in ALLOWED_VISUALS
                and all(visual.get(f) for f in VISUAL_REQUIRED_FIELDS.get(visual.get("type"), []))
            )
            if not valid:
                card.pop("visual", None)  # 형식이 안 맞으면 없느니만 못하니 제거(선택 필드)

        if card.get("type") in ("why", "concept", "meaning") and card.get("num"):
            concept_num += 1
            card["num"] = "①②③④⑤"[min(concept_num, 5) - 1]
    return data


_STRAY_SCRIPT_RE = re.compile(r"[぀-ヿ㐀-鿿]")  # 히라가나/가타카나 + 한자(간지/한지)


def _check_no_stray_script(data: dict) -> None:
    """모델이 가끔 한국어 문장 중간에 일본어·한자를 잘못 끼워 넣는 경우가
    실측으로 두 건 확인됨(2026-09-06): "관심을 가지기" 자리에
    "관심を持기"(히라가나 조사+한자 동사), "영향" 자리에 "影響"(한자
    그대로)이 나옴. 이 브랜드의 카드 카피는 항상 순수 한글(+영어 약어·숫자)만
    쓰므로, 한자·가나가 하나라도 섞이면 오류로 보고 예외를 던져 재생성을
    유도한다. validate.js에는 문자셋 검사가 없어 그대로 통과했던 것."""
    for card in data.get("cards", []):
        texts = [
            card.get(k) for k in ("badge", "hook", "sub", "eyebrow", "title", "body")
        ]
        texts.extend(card.get("items") or [])
        for text in texts:
            if text and _STRAY_SCRIPT_RE.search(text):
                raise ValueError(f"카드 텍스트에 일본어 문자 혼입 감지: {text!r}")


NEAR_DUPLICATE_THRESHOLD = 85


def _check_no_duplicate_cards(data: dict) -> None:
    """LLM이 같은 내용을 카드 2장에 나눠 그대로 반복하는 경우가 실제로
    확인돼(운영자 피드백, 2026-09-06 — 반도체 카드 2번/3번이 제목·본문 모두
    동일), cover/disclaimer/action을 제외한 본문 카드끼리 내용이 겹치면
    예외를 던져 재생성을 유도한다(process_item의 재시도 루프가 처리).

    완전 일치뿐 아니라 "제목만 바꾸고 사실상 같은 문장을 반복"하는 근접
    중복도 잡기 위해, clean_news.py의 기사 중복 제거와 동일한 rapidfuzz
    유사도 비교를 body 텍스트에 적용한다(카드 역할을 concept/why/meaning으로
    세분화하면서 생긴 반복 위험 — 위 설계 결정 참고)."""
    bodies: list[str] = []
    for card in data.get("cards", []):
        if card.get("type") in ("cover", "disclaimer", "action"):
            continue
        title, body = card.get("title"), card.get("body")
        if (title, body) == (None, None):
            continue
        combined = f"{title or ''} {body or ''}".strip()
        for prev in bodies:
            if fuzz.token_sort_ratio(combined, prev) >= NEAR_DUPLICATE_THRESHOLD:
                raise ValueError(f"카드 내용 근접 중복 감지(title={title!r})")
        bodies.append(combined)


FEWSHOT_EXAMPLE_BODIES = [
    "HBM은 반도체용 '급행 택배 전용도로'예요. 일반 도로 대신 전용도로를 깔면 택배가 훨씬 빨리 도착하듯, AI가 필요한 데이터를 더 빨리 받을 수 있어요.",
    "기준금리는 은행들의 '대출 도매가' 같은 거예요. 마트 도매가가 그대로면 손님 가격도 그대로이듯, 동결되면 대출·예금 이자도 당분간 크게 안 변해요.",
]
FEWSHOT_ECHO_THRESHOLD = 80


def _check_no_prompt_echo(data: dict) -> None:
    """소형/중형 모델이 시스템 프롬프트의 few-shot 비유 예시 문장을 그대로
    베껴 쓰는 경향이 실측으로 확인됨(예: 금리 관련 기사마다 매번 "대출
    도매가" 비유를 토씨 하나 안 틀리고 재사용) — "매번 다른 비유를 만들어라"
    지시만으로는 부족해서, 코드로 예시 문장과의 유사도를 검사해 강제로
    재생성을 유도한다(few-shot 예시가 실제 출력에 새는 것을 막는 안전망)."""
    for card in data.get("cards", []):
        body = card.get("body")
        if not body:
            continue
        for example in FEWSHOT_EXAMPLE_BODIES:
            if fuzz.token_sort_ratio(body, example) >= FEWSHOT_ECHO_THRESHOLD:
                raise ValueError(f"프롬프트 예시 문장을 그대로 베낌(title={card.get('title')!r})")


def build_content_json(title: str, article_text: str, category: str) -> dict:
    label = config.CATEGORY_LABELS.get(category, category)
    system_prompt = SYSTEM_PROMPT.replace(
        "__POSES__", "\n".join(f"- {p}" for p in EXISTING_POSES)
    ).replace("__CATEGORY__", label)
    article_block = article_text if article_text else "(원문을 가져오지 못함, 제목 정보만으로 신중하게 작성)"
    user_content = f"제목: {title}\n\n[기사 원문]\n{article_block}"

    raw = call_text_model(system_prompt, user_content, max_tokens=1800)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    _check_no_duplicate_cards(data)
    _check_no_prompt_echo(data)
    _check_no_stray_script(data)
    data["season"] = current_season()
    data["handle"] = config.INSTAGRAM_HANDLE
    return sanitize_cards(data)


def check_content_quality(content: dict, caption: str, article_text: str) -> None:
    """업로드 전 편집 품질 게이트. 실패하면 폴백 게시 대신 재생성한다."""
    cards = content.get("cards") or []
    types = [card.get("type") for card in cards]
    if len(article_text.strip()) < config.QA_MIN_ARTICLE_CHARS:
        raise ValueError(f"원문이 QA 최소 분량 미달: {len(article_text.strip())}자")
    if not 4 <= len(cards) <= 6:
        raise ValueError("QA: 카드 수는 4~6장이어야 함")
    if types.count("cover") != 1 or types.count("disclaimer") != 1:
        raise ValueError("QA: 표지와 고지 카드는 각각 1장이어야 함")
    if "concept" not in types or "why" not in types:
        raise ValueError("QA: 개념 설명과 사실 근거 카드가 필요함")
    if not caption or len(caption.strip()) < 40:
        raise ValueError("QA: 캡션이 너무 짧음")


def check_rendered_images(out_dir: Path, expected_count: int) -> None:
    pngs = sorted(out_dir.glob("[0-9][0-9].png"))
    if len(pngs) != expected_count:
        raise ValueError(f"QA: 렌더 이미지 수 불일치({len(pngs)}/{expected_count})")
    for image_path in pngs:
        with Image.open(image_path) as image:
            if image.size != (1080, 1350):
                raise ValueError(f"QA: 잘못된 이미지 크기 {image_path.name}: {image.size}")
            if max(ImageStat.Stat(image.convert("RGB")).var) < 2:
                raise ValueError(f"QA: 빈 이미지 의심: {image_path.name}")


def validate_content(content_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["node", "assets/templates/validate.js", str(content_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return result.returncode == 0, (result.stdout + result.stderr)


def render_cardset(content_path: Path, out_dir: Path) -> bool:
    result = subprocess.run(
        ["node", "assets/templates/render.js", str(content_path), str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        log.error("렌더 실패: %s", (result.stdout or "") + (result.stderr or ""))
        return False
    log.info((result.stdout or "").strip())
    return True


def _record_retry(item: dict, attempt: int, reason: str) -> None:
    events.record(
        "cardset", "retry", category=item["category"], news_item_id=item["id"],
        message=f"({attempt}/{MAX_ATTEMPTS}) {reason}",
    )


def process_item(item: dict) -> dict | None:
    log.info("[%s] 기사 원문 수집 중: %s", item["category"], item["url"])
    events.record("cardset", "started", category=item["category"], news_item_id=item["id"], message=item["title"])
    article_text = fetch_article_text(item["url"])
    log.info("[%s] 원문 %d자 확보", item["category"], len(article_text))

    caption = None
    content = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = build_content_json(item["title"], article_text, item["category"])
            caption = data.pop("caption", "")
            content = data
        except Exception as e:
            log.warning("[%s] LLM 응답 생성/검사 실패(%d/%d): %s", item["category"], attempt, MAX_ATTEMPTS, e)
            _record_retry(item, attempt, f"LLM 응답 생성/검사 실패: {e}")
            continue

        out_dir = Path("output") / date.today().isoformat() / item["category"]
        out_dir.mkdir(parents=True, exist_ok=True)
        content_path = out_dir / "content.json"
        content_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            check_content_quality(content, caption or "", article_text)
        except ValueError as e:
            log.warning("[%s] 콘텐츠 QA 실패(%d/%d): %s", item["category"], attempt, MAX_ATTEMPTS, e)
            _record_retry(item, attempt, f"콘텐츠 QA 실패: {e}")
            continue

        ok, message = validate_content(content_path)
        if not ok:
            log.warning("[%s] 검증 실패(%d/%d): %s", item["category"], attempt, MAX_ATTEMPTS, message)
            _record_retry(item, attempt, f"검증 실패: {message.strip()[:300]}")
            continue

        log.info("[%s] 검증 통과 (%d/%d)", item["category"], attempt, MAX_ATTEMPTS)
        if not render_cardset(content_path, out_dir):
            log.warning("[%s] 렌더 실패(오버플로 등), 콘텐츠 재생성으로 재시도(%d/%d)", item["category"], attempt, MAX_ATTEMPTS)
            _record_retry(item, attempt, "렌더 실패(오버플로 등)")
            continue
        try:
            check_rendered_images(out_dir, len(content["cards"]))
        except ValueError as e:
            log.warning("[%s] 렌더 QA 실패(%d/%d): %s", item["category"], attempt, MAX_ATTEMPTS, e)
            _record_retry(item, attempt, f"렌더 QA 실패: {e}")
            continue
        (out_dir / "caption.txt").write_text(caption or "", encoding="utf-8")
        return {"content_path": content_path, "out_dir": out_dir, "caption": caption}

    log.error("[%s] QA를 %d회 통과하지 못해 업로드하지 않음", item["category"], MAX_ATTEMPTS)
    events.record(
        "cardset", "failed", category=item["category"], news_item_id=item["id"],
        message=f"QA {MAX_ATTEMPTS}회 미통과로 게시하지 않음 (원문 {len(article_text)}자)",
    )
    return None


def build_fallback_content(item: dict, article_text: str) -> dict:
    """LLM이 계속 실패할 때도 카테고리가 그날 통째로 빠지지 않도록, 제목과
    원문 첫 문장만으로 구조 규칙을 100% 만족하는 최소 카드 3장을 코드로 만든다."""
    label = config.CATEGORY_LABELS.get(item["category"], item["category"])
    first_sentence = (article_text.split(".")[0].strip() + "." if article_text else item["title"])[:90]
    return {
        "season": current_season(),
        "handle": config.INSTAGRAM_HANDLE,
        "cards": [
            {
                "type": "cover",
                "badge": "오늘의 경제 🐾",
                "hook": _truncate(item["title"], 20),
                "char": "cat-default",
            },
            {
                "type": "why",
                "eyebrow": label,
                "title": _truncate(item["title"], 36),
                "body": _truncate(first_sentence, 90),
                "char": "cat-explain",
            },
            {
                "type": "disclaimer",
                "brand": "출처 & 안내",
                "text": "이 콘텐츠는 교육·정보 제공용이며, 특정 투자 권유나 개인 맞춤 재무 조언이 아닙니다.",
                "note": "자세한 출처는 캡션에서 확인하세요",
                "handle": config.INSTAGRAM_HANDLE,
            },
        ],
    }


def build_and_render_fallback(item: dict, article_text: str) -> dict | None:
    content = build_fallback_content(item, article_text)
    out_dir = Path("output") / date.today().isoformat() / item["category"]
    out_dir.mkdir(parents=True, exist_ok=True)
    content_path = out_dir / "content.json"
    content_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, message = validate_content(content_path)
    if not ok:
        log.error("[%s] 폴백 카드세트도 검증 실패(코드 버그 가능성): %s", item["category"], message)
        return None
    if not render_cardset(content_path, out_dir):
        log.error("[%s] 폴백 카드세트 렌더도 실패, 완전히 건너뜀", item["category"])
        return None

    caption = f"{item['title']}\n\n#경제 #카드뉴스 #묘한경제"
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    return {"content_path": content_path, "out_dir": out_dir, "caption": caption}


def main():
    client = get_client()
    items = fetch_approved_without_cardset()
    log.info("카드세트 생성 대상: %d건", len(items))

    publish_order = (client.table("cards").select("id", count="exact").execute().count or 0) + 1

    for item in items:
        result = process_item(item)
        if not result:
            continue

        pngs = sorted(result["out_dir"].glob("[0-9][0-9].png"))
        card = client.table("cards").insert(
            {
                "news_item_id": item["id"],
                "category": item["category"],
                "final_title": item["title"][: config.TITLE_MAX_CHARS],
                "image_path": str(result["out_dir"]),
                "publish_order": publish_order,
                "published": False,
            }
        ).execute().data[0]
        publish_order += 1
        log.info("[%s] 카드세트 생성 완료: %s (%d장)", item["category"], result["out_dir"], len(pngs))

        # 대시보드 [카드] 미리보기용. 게시 단계도 같은 경로에 upsert하므로 중복 업로드는 무해하다.
        try:
            for png in pngs:
                upload_to_storage(str(png))
        except Exception as e:
            log.warning("[%s] 미리보기 이미지 업로드 실패(게시에는 영향 없음): %s", item["category"], e)
        events.record(
            "cardset", "success", category=item["category"], count=len(pngs), news_item_id=item["id"],
            card_id=card["id"], message=f"카드 {len(pngs)}장 렌더링 완료",
        )


if __name__ == "__main__":
    main()

"""기사 원문 → 카드뉴스 슬라이드 JSON (2026-10-29 내용 가공 재설계, 템플릿 시안용).

기존 generate_cardset.py는 표지·개념 비유·근거·의미·행동 제안·고지를 매번 같은
순서로 채우게 해서, 기사에 없는 내용으로 빈칸을 메우는 일이 잦았다(운영자 피드백:
"불필요한 정보가 질 낮게 들어 있다"). 여기서는:

- 슬라이드 수(채널 설정, 경제 3~7)와 종류(cover/stat/compare/point/list/quote/closing)를 기사에
  맞춰 모델이 고른다. 고정 페이지 없음. 한 장 = 메시지 하나.
- 숫자는 원문에 있는 것만 쓴다. 코드가 슬라이드 속 숫자를 원문과 대조해
  없으면 재생성한다(LLM=품질, 코드=제약 원칙).
- 투자 유의 문구는 슬라이드가 아니라 캡션 끝에 코드가 붙인다.
- 기사 제목의 한자 약어(美·中 등)는 모델에 넘기기 전에 한글로 푼다.

출력 형식은 card_templates/의 공용 슬라이드 스키마를 따른다.

실행(시안 확인용):
    python slide_writer.py output/template-sample/article.json output/template-sample/deck.json
"""

import ast
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import httpx
from rapidfuzz import fuzz

import comment_dm
import config
from llm_client import call_text_model

MAX_ATTEMPTS = 3
# 2026-10-29 비교: llama-3.3-70b는 같은 숫자 반복·기사체·표지 반복이 심해 gpt-oss-120b를 기본으로 둔다.
MODEL = os.environ.get("SLIDE_WRITER_MODEL", "@cf/openai/gpt-oss-120b")
DISCLAIMER = config.CHANNEL_CONFIG["disclaimer"]  # 채널별. 비어 있으면 붙이지 않음
# 채널별 장수·캡션 방식. 청년 채널은 카드 1~4장 + 자세한 캡션(2026-10-30 운영자), 경제는 기존 3~7장 + 짧은 캡션.
SLIDES_MIN, SLIDES_MAX = config.CHANNEL_CONFIG.get("slide_range", [3, 7])
CAPTION_SPEC = config.CHANNEL_CONFIG.get("caption_spec", "인스타 캡션 본문(3~5문장, 해시태그 제외)")
CHECK_CAPTION = config.CHANNEL_CONFIG.get("check_caption", False)  # 캡션에 사실을 많이 담는 채널은 캡션 숫자도 원문과 대조
CAPTION_MAX = config.CHANNEL_CONFIG.get("caption_max", 2000)

HANJA_ABBREVIATIONS = {
    "美": "미국", "中": "중국", "日": "일본", "韓": "한국", "北": "북한", "英": "영국",
    "獨": "독일", "佛": "프랑스", "露": "러시아", "印": "인도", "豪": "호주", "臺": "대만",
    "台": "대만", "歐": "유럽", "亞": "아시아", "與": "여당", "野": "야당", "前": "전", "現": "현",
}
_STRAY_SCRIPT_RE = re.compile(r"[぀-ヿ㐀-鿿]")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_BOILERPLATE_RE = re.compile(r"<?ⓒ.*$|무단전재.*$", re.MULTILINE)
# 운영자가 질 낮다고 지적한 뻔한 마무리·투자 암시 표현(2026-10-29)
BANNED_PHRASES = ("오를 가능성", "염두에", "기대해 볼", "주목해 보세요", "기억해 주세요", "핵심을 기억",
                  "공부해 보", "관심을 가져", "투자에 유의", "알아두면 좋",
                  # 번역투·기사체·과장(가독성 피드백, 2026-10-29)
                  "것으로 나타났", "것으로 보인다", "이같은", "에 있어서", "라고 할 수 있", "폭등", "폭락", "충격", "대박",
                  # 투자 권유형(2026-10-31 경제 계정 정리: "반도체 수혜 지역 투자에 주목하세요"가 실제로 게시됨)
                  "주목하세요", "주목해야", "사세요", "매수", "수혜주", "수혜 지역", "지금이 기회", "투자하세요", "추천 종목",
                  "담아 두세요", "올라타")
# 채널별 말투 금지어(청년 채널: 압박·공포 표현, 2026-10-30). 경제는 설정이 없어 그대로.
BANNED_PHRASES += tuple(config.CHANNEL_CONFIG.get("banned_phrases", ()))
TITLE_LINE_MAX = 13  # 템플릿 제목 크기에서 한 줄에 무리 없이 들어가는 글자 수
SENTENCE_MAX = 40
HOOK_MAX = 24  # chat·notify·desktop 표지의 말풍선·알림 한 줄
SENTENCE_FIELDS = ("body",)  # 문장으로 쓰는 필드. label·note·kicker는 명사구 허용
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_POLITE_END_RE = re.compile(r"(요|죠|까|나요)[.!?…]*$")

# --- 2026-10-30 QA 보완 ---
# 기존 숫자 대조는 두 자리 이상만 봐서 "3월 5일", "1억", "5%"처럼 한 자리 숫자가 붙은 날짜·금액이 틀려도 통과했다.
_DATE_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_ARTICLE_DATE_RES = (_DATE_RE, re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)"))  # 원문은 "11.14" 표기도 인정
_UNIT_NUMBER_RE = re.compile(r"(?<![\d.,])(\d(?:\.\d+)?)\s*(억|천만|천|만|%|배|세|살)")
_YMD_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B55\uFE0F]")
KST = timezone(timedelta(hours=9))
NO_EMOJI_ON_CARDS = config.CHANNEL_CONFIG.get("no_emoji_on_cards", False)  # 카드 디자인에 이모지 없는 채널
PLAN_WORDS = tuple(config.CHANNEL_CONFIG.get("plan_notice_words", ()))  # 원문에 있으면 "예정" 표시가 필요한 말
PLAN_MARKERS = ("예정", "정부안", "확정 전", "확정되면", "추진", "검토")
CHECK_DEADLINE = config.CHANNEL_CONFIG.get("check_deadline", False)  # 모집·신청 정보 채널은 마감 지난 기사를 건너뜀
# 원문에 없으면 쓰면 안 되는 단정(인원 제한·접수 방법 등). 숫자가 없어 숫자 대조로는 못 잡는다(2026-10-30 한남대 박람회 덱).
CLAIM_WORDS = tuple(config.CHANNEL_CONFIG.get("claim_words", ()))
CAPTION_CLOSING_WORDS = tuple(config.CHANNEL_CONFIG.get("caption_closing_words", ()))  # 캡션 마지막 줄의 친구 태그·공유 부탁
# "·" 목록 속 줄임말 → 원문 표기(2026-11-01 "기초·한부·북한·…·의사"; "의사"는 의사상자인데 의사로 읽힘). 원문에 원래 표기가 있을 때만 막는다.
LIST_ABBREVIATIONS = dict(config.CHANNEL_CONFIG.get("list_abbreviations", {}))
_HANGUL_RE = re.compile(r"[가-힣]")
_IMAGE_PROMPT_EXAMPLE = "English. A concrete photographic scene"  # 출력 형식 예시 문구. 모델이 그대로 베끼기도 한다
CAPTION_HARD_MAX = 2200  # 인스타그램 캡션 한도(고지·해시태그 포함)

# --- 2026-10-30 지난 행사 보강(한남대 채용박람회: 10/29에 끝난 행사를 "29일 열려"로 씀) ---
# 월 없이 일만 쓴 날짜("29일", "오는 2일"). "10월 29일"의 일, "3일간"·"5일 이내" 같은 기간은 빼고 본다.
_DAY_ONLY_RE = re.compile(r"(?:(오는|지난)\s*)?(?<![\d.월])(?<!월\s)(\d{1,2})\s*일(?!\s*(?:간|동안|이내|전|후|마다|째|치))")
# 행사일·마감일로 보는 문장의 표지. 이 말이 있는 문장(캡션은 줄)의 날짜만 지났는지 본다("1일부터 시행" 같은 시작일은 빼려고).
EVENT_DATE_WORDS = ("🗓", "마감", "기간", "접수", "신청", "까지", "일시", "개최", "열려", "열린", "열리", "행사", "박람회", "설명회", "면접")
# 원문이 이미 끝난 행사를 전하는 사후 보도인지(채널 설정, 정규식). 앞으로의 날짜가 원문에 없을 때만 건너뛴다.
_POST_EVENT_RE = re.compile("|".join(config.CHANNEL_CONFIG.get("post_event_patterns", ()))) if config.CHANNEL_CONFIG.get("post_event_patterns") else None
CAPTION_DATE_MARK = config.CHANNEL_CONFIG.get("caption_date_mark", "")  # 원문에 날짜·기간이 있으면 캡션에 있어야 할 줄 표시(🗓)
_PERIOD_RE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}\s*일\s*(?:부터|까지)|(?:오는|지난)\s*\d{1,2}\s*일|한\s*달\s*간|\d+\s*(?:주|개월)\s*간")
# 표지 subtitle이 "29일"처럼 숫자·날짜 한 덩어리뿐인지(이것만 빼면 아무 말도 안 남는지)
_BARE_SUBTITLE_RE = re.compile(r"[\d\s.,~\-/%:()]|년|월|일|시|분|원|만|억|명|개|건|세|살|배|주")

# --- 2026-10-31 경제 계정 정리(캡션이 "기사제목 - 매체명" + #경제 #카드뉴스, 해시태그 한자, "이런 일이일까요") ---
# Google News 제목 끝의 " - 매체명"·"| 매체명" 꼬리표. 모델에 넘기기 전에 떼고, 캡션에 남아 있으면 실패.
_OUTLET_TAIL_RE = re.compile(r"\s+(?:-|–|—|\||ㅣ)\s*[^\s\-|–—ㅣ][^\n\-|–—ㅣ]{0,24}$")
_CAPTION_TAIL_RE = re.compile(r"\s(?:-|–|—|\||ㅣ)\s*[가-힣A-Za-z][가-힣A-Za-z0-9 .&]{0,19}$")
CAPTION_RULES = config.CHANNEL_CONFIG.get("caption_rules", False)  # 훅→3줄 요약→지갑 영향→참고 구조(경제)
CALC_PREFIX = config.CHANNEL_CONFIG.get("calc_line_prefix", "")  # 이 말로 시작하는 캡션 한 줄은 계산 예시라 원문 숫자 대조에서 뺀다
HOOK_KEYWORDS = tuple(config.CHANNEL_CONFIG.get("hook_keywords", ()))
HASHTAG_RULES = {"min": 0, "max": 5, "banned": [], **config.CHANNEL_CONFIG.get("hashtag_rules", {})}  # 인스타 해시태그 상한 5
_HASHTAG_RE = re.compile(r"[가-힣A-Za-z0-9]+")  # 한자·가나·중국어 간체 등 한글 아닌 CJK 문자 차단
WALLET_WORDS = ("월급", "대출", "적금", "예금", "세금", "연금", "보험", "이자", "생활비", "지갑", "환급", "연봉", "월세", "전세", "카드값", "저축")
# 어색한 어미·중복 조사(맞춤법 간단 점검). 반복 낱말("금리 금리")은 경제 채널만 본다(반말 채널은 "진짜 진짜"를 일부러 쓴다).
_SPELLING_RE = re.compile(r"이일까|이일지|을를|를을|은는|는은|과와|와과")
_REPEAT_WORD_RE = re.compile(r"(?<![가-힣])([가-힣]{2,})\s+\1(?![가-힣])")


class SkipArticle(Exception):
    """다시 써도 해결되지 않는 문제(이미 마감된 모집 등). 재시도 없이 이 기사를 건너뛴다."""


class NoCalculation(ValueError):
    """금액 계산 카드가 없음. 한 번 다시 쓰게 하고, 그래도 없으면 기사를 건너뛴다(다음 순위 후보로)."""


# --- 금액 계산(2026-11-02 경제: 카드마다 사회초년생 기준 실제 금액) ---
# 덱의 "calculations"에 계산식을 남기게 하고 코드가 다시 계산해 맞춰 본다. 재료는 원문 숫자, 원문에 없는 값은
# 카드에 밝힌 가정(연봉·월급은 calc_persona 범위, 그 밖엔 딱 떨어지는 예시 금액)만. 검증된 결과·가정 숫자는
# "원문에 없는 숫자" 검사에서 뺀다.
REQUIRE_CALC = config.CHANNEL_CONFIG.get("require_calc", False)
CALC_PERSONA = config.CHANNEL_CONFIG.get("calc_persona", {})
_MULTIPLIER = {"억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4, "천": 10**3}
_WON_RE = re.compile(r"(?:\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|만|천)\s*)*\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|만|천)?\s*원")
_WON_PART_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만|천)?")
_PERCENT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%p|%P|％|%|bp)")
_CALC_CONSTANTS = {1, 2, 3, 4, 6, 10, 12, 24, 36, 52, 60, 100, 365, 1000}  # 개월·해·백분율 환산에 쓰는 수
_ROUND_RE = re.compile(r"^[1-9](?:0*|50*)$")  # 예시 금액은 1·5로 시작하는 딱 떨어지는 값(만 원 단위 이상)


def won_value(text: str) -> float:
    """"1억 2,000만 원" → 120000000. 원 금액이 아니면 ValueError."""
    m = _WON_RE.search(text)
    if not m:
        raise ValueError(f"원 단위 금액이 아님: {text!r}")
    return sum(float(n.replace(",", "")) * _MULTIPLIER.get(u, 1) for n, u in _WON_PART_RE.findall(m.group(0)))


def amount_value(text: str) -> float:
    """계산 재료 값: 원 금액, 퍼센트(4.88% → 0.0488, 1%p → 0.01, 25bp → 0.0025), 그 밖의 수."""
    text = str(text)
    if _WON_RE.search(text):
        return won_value(text)
    if (m := _PERCENT_RE.search(text)):
        return float(m.group(1).replace(",", "")) / (10000 if m.group(2) == "bp" else 100)
    if (m := _WON_PART_RE.search(text)):
        return float(m.group(1).replace(",", "")) * _MULTIPLIER.get(m.group(2), 1)
    raise ValueError(f"숫자를 읽을 수 없음: {text!r}")


def article_values(article: str) -> list[float]:
    """원문에 나온 금액·퍼센트·수(만·억 단위 포함). 계산 재료가 원문 값인지 볼 때 쓴다."""
    values = [won_value(m.group(0)) for m in _WON_RE.finditer(article)]
    values += [float(n.replace(",", "")) / (10000 if u == "bp" else 100) for n, u in _PERCENT_RE.findall(article)]
    values += [float(n.replace(",", "")) * _MULTIPLIER.get(u, 1) for n, u in _WON_PART_RE.findall(article)]
    return values


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _eval_formula(formula: str, names: dict[str, float]) -> float:
    """+ - * / 괄호와 재료 이름, 환산용 상수만 허용하는 계산."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = walk(node.left), walk(node.right)
            return {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right}.get(type(node.op)) \
                if not isinstance(node.op, ast.Div) else left / right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value in _CALC_CONSTANTS:
            return float(node.value)
        raise ValueError(f"계산식에 쓸 수 없는 값 {ast.unparse(node)!r}(재료 이름과 12·100 같은 환산 수만)")
    return walk(ast.parse(formula.replace("×", "*").replace("÷", "/"), mode="eval"))


def _display_unit(text: str) -> float:
    """"5만 원"은 1만 원, "3,600원"은 1원 단위로 반올림했다고 본다."""
    parts = _WON_PART_RE.findall(_WON_RE.search(text).group(0))
    unit = _MULTIPLIER.get(parts[-1][1], 1)
    decimals = parts[-1][0].split(".")[1] if "." in parts[-1][0] else ""
    return unit / (10 ** len(decimals))


def strip_verified(text: str, values: list[float]) -> str:
    """검증된 계산 결과·가정 금액(원 단위)을 문구에서 지운다. 남은 부분만 "원문에 없는 숫자" 검사를 받는다.
    "27만2천원"·"27.2만 원"처럼 표기가 달라도 값이 같으면 지운다."""
    if not values:
        return text
    return _WON_RE.sub(lambda m: "" if any(abs(won_value(m.group(0)) - v) <= max(1.0, v * 0.01) for v in values)
                       else m.group(0), text)


def check_calculations(deck: dict, article: str) -> list[float]:
    """덱의 calculations를 검증하고, 원문에 없어도 되는 금액(검증된 결과·카드에 밝힌 가정)을 돌려준다.
    금액 계산이 꼭 필요한 채널인데 카드에 보이는 원 단위 계산 결과가 하나도 없으면 NoCalculation."""
    calcs = deck.get("calculations") or []
    if not isinstance(calcs, list):
        raise ValueError("calculations는 목록이어야 함")
    slides = deck.get("slides") or []
    values = article_values(article)
    allowed: list[float] = []
    shown = 0
    for n, calc in enumerate(calcs, 1):
        where = f"계산 {n}"
        if not isinstance(calc, dict) or not isinstance(calc.get("inputs"), dict):
            raise ValueError(f"{where}: {{\"slide\", \"inputs\", \"formula\", \"result\"}} 형식이 아님: {calc!r}")
        slide_no = calc.get("slide")
        if not (isinstance(slide_no, int) and 1 <= slide_no <= len(slides)):
            raise ValueError(f"{where}: slide 번호가 잘못됨({slide_no!r})")
        slide_text = " ".join(_texts(slides[slide_no - 1]))
        names: dict[str, float] = {}
        for name, spec in calc["inputs"].items():
            spec = spec if isinstance(spec, dict) else {"value": spec, "source": "원문"}
            value_text = str(spec.get("value") or "")
            value = amount_value(value_text)
            if not str(name).isidentifier():
                raise ValueError(f"{where}: 재료 이름 {name!r}은 영문 한 단어로")
            if spec.get("source") == "가정" and not _WON_RE.search(value_text) and value in _CALC_CONSTANTS:
                pass  # "12개월"·"1년" 같은 환산 수는 가정으로 적어도 그대로 쓴다
            elif spec.get("source") == "가정":
                label = str(spec.get("label") or "")
                salary_key = "annual_salary" if "연봉" in label else "monthly_salary" if "월급" in label else None
                if salary_key and CALC_PERSONA.get(salary_key):
                    low, high = CALC_PERSONA[salary_key]
                    if not low <= value <= high:
                        raise ValueError(f"{where}: 가정 {label} {value_text}이 사회초년생 범위({low:,}~{high:,}원) 밖")
                elif not _ROUND_RE.match(f"{int(value // 10000)}" if value >= 10000 else "x"):
                    raise ValueError(f"{where}: 가정 금액 {value_text}은 딱 떨어지는 예시 금액(100만 원, 1,000만 원처럼)만")
                if not any(_same(value, won_value(m.group(0))) for m in _WON_RE.finditer(slide_text)):
                    raise ValueError(f"{where}: 가정 {label} {value_text}을 {slide_no}장에 밝히지 않음(예: \"{label} {value_text} 기준\")")
                allowed.append(value)
            elif not any(_same(value, v) for v in values):
                raise ValueError(f"{where}: 재료 {value_text!r}이 원문에 없음(원문 숫자만, 아니면 source를 \"가정\"으로 밝힐 것)")
            names[str(name)] = value
        computed = _eval_formula(str(calc.get("formula") or ""), names)
        result_text = str(calc.get("result") or "")
        result = won_value(result_text)
        tolerance = max(_display_unit(result_text) / 2, abs(computed) * 0.01)
        if abs(abs(computed) - result) > tolerance:
            raise ValueError(f"{where}: {calc.get('formula')} = {computed:,.0f}원인데 결과를 {result_text}으로 씀")
        if not any(abs(won_value(m.group(0)) - result) <= tolerance for m in _WON_RE.finditer(slide_text)):
            raise ValueError(f"{where}: 결과 {result_text}이 {slide_no}장 문구에 없음")
        allowed.append(result)
        # 원문에 이미 나온 금액을 옮겨 적은 건 계산 결과로 치지 않는다(11/2 전세대출 덱: 원문 "5만원가량 싸다"를 그대로).
        uses_assumption = any(isinstance(v, dict) and v.get("source") == "가정" for v in calc["inputs"].values())
        if uses_assumption or not any(abs(result - v) <= max(1.0, v * 0.01) for v in values):
            shown += 1
    if REQUIRE_CALC and not shown:
        raise NoCalculation("카드에 사회초년생 기준 원 단위 계산 결과가 없음(예: \"월 3만 원\", \"연 36만 원\"). 원문에 이미 "
                            "나온 금액을 옮긴 건 계산이 아님. 기사 숫자로 독자 한 사람의 한 달·1년 금액을 계산해 한 장 이상에 넣고 "
                            "calculations에 계산식을 남길 것")
    return allowed

SYSTEM_PROMPT = config.CHANNEL_CONFIG["writer_intro"] + """
기사 하나를 읽고, 스크롤을 멈추게 하는 캐러셀 슬라이드를 만든다.

[가장 중요한 원칙]
1. 이 기사에서 독자가 꼭 가져가야 할 핵심 메시지 딱 1개를 먼저 정하고, 모든 슬라이드는 그 메시지를 위해서만 존재한다.
2. 핵심과 관계없는 정보(배경 설명, 부수적 인물, 매체 이름, 일반론)는 과감히 버린다. 기사에 있다고 다 쓰지 않는다.
3. 장수는 내용이 정한다. 3~7장. 할 말이 3장이면 3장으로 끝낸다. 같은 말을 나눠서 늘리지 않는다.
4. 숫자는 기사에 나온 숫자만, 기사와 똑같은 값으로 쓴다. 숫자를 만들거나 반올림하거나 계산하지 않는다.
5. "공부해 보세요", "관심을 가져보세요", "투자에 유의하세요" 같은 뻔한 마무리 문장 금지. 투자 권유·종목 추천 금지.
6. 어려운 용어는 핵심을 이해하는 데 꼭 필요할 때만, 한 문장 비유로 짧게 푼다.
7. 한자·일본어 금지. 한글, 숫자, 영어 약어(S&P500, ETF 등)만 쓴다.
8. 말투는 친근한 해요체("~예요", "~했어요"). 한 문장은 짧게. 기사체("~했다", "~로 나타났다") 금지.
9. 같은 숫자는 덱 전체에서 최대 2번만 쓴다. 같은 사실을 장마다 되풀이하지 않는다.
10. 기사에 반전·단서·반론("다만", "하지만", "오히려")이 있으면 반드시 한 장에 담는다. 독자가 오해하지 않게 하는 장이다.
11. 표지 title은 독자가 "왜?" "진짜?"라고 묻게 만드는 한 문장이다. 사실 나열형 제목("○○ 후 ○○") 금지.
12. closing은 표지를 반복하지 않는다. 앞 장들을 다 본 독자에게 남길 한 줄(그래서 무엇을 기억하면 되는지)을 새 표현으로 쓴다.
13. 앞으로 가격이 오르거나 내릴 거라고 암시하지 않는다("오를 가능성", "기대해 볼 만", "염두에 두세요" 금지). 과거 사실과 그 한계만 전한다.
14. 비교할 때는 기사에 나온 가장 의미 있는 기준(평소·다른 해·이전 기간 등)과 비교한다. 서로 다른 자산을 나란히 놓는 것보다 "평소 대비"가 더 설득력 있다.
15. 표본이 적거나 인과가 불확실하다는 내용이 기사에 있으면, 그 한계를 숫자와 함께 분명히 말한다.
{channel_rules}
[슬라이드 종류] 필요한 것만 골라 쓴다. 첫 장은 반드시 cover.
- 모든 title·quote는 의미 단위로 "\\n"을 넣어 줄을 나눈다. 한 줄은 12자 이내(예: "중간선거 끝나면\\n비트코인이 오른다?").
- cover: 표지. title은 궁금증을 만드는 한 문장(최대 24자). kicker는 위에 붙는 짧은 꼬리표(최대 12자). subtitle은 선택(최대 30자).
- stat: 숫자 하나로 말하는 장. value(예: "53.7%"), label(그 숫자가 무엇인지, 최대 30자), note(선택, 최대 50자).
- compare: 두세 값을 나란히 비교. title(최대 22자), items=[{"label": 최대 12자, "value": 최대 10자}], note(선택).
- point: 문장으로 설명하는 장. kicker(선택), title(최대 22자), body(최대 90자, 문장 2개 이내).
- list: 짧은 항목 나열. title(최대 22자), items(2~4개, 각 최대 28자).
- quote: 기사 속 인용·핵심 한 줄을 크게. quote(최대 50자), source(선택).
- closing: 마지막 장. 핵심 메시지를 한 문장으로 다시 꽂는다. title(최대 24자), body(선택, 최대 60자).

[출력] 아래 JSON 객체 하나만 출력한다. 다른 말 금지.
{
  "message": "핵심 메시지 한 문장",{verdict_fields}
  "slides": [ {"kind": "cover", ...}, ... ],
  "caption": "인스타 캡션 본문(3~5문장, 해시태그 제외)",
  "hashtags": ["{hashtag_example}", "..."],
  "image_prompt": "English. A concrete photographic scene (subject, setting, light, mood) that fits the topic. No text, no logos, no charts."
}
"""
# 채널별 추가 규칙·해시태그 예시. JSON 예시의 중괄호 때문에 format 대신 replace로 채운다.
if REQUIRE_CALC:  # 금액 계산 채널(경제): "계산하지 않는다"를 바꾸고 계산 규칙·출력 칸을 붙인다(2026-11-02)
    SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
        "4. 숫자는 기사에 나온 숫자만, 기사와 똑같은 값으로 쓴다. 숫자를 만들거나 반올림하거나 계산하지 않는다.",
        "4. 숫자는 기사에 나온 숫자만, 기사와 똑같은 값으로 쓴다. 단 [금액 계산] 카드는 기사 숫자로 계산한 결과를 쓰고 계산식을 남긴다.",
    ).replace("[슬라이드 종류]", """[금액 계산] 이 계정의 핵심이다. 카드 1장 이상에 사회초년생 기준 원 단위 계산 결과를 넣는다(예: "월 3만 원 더", "1년이면 36만 원").
- 계산 재료는 기사 숫자만 쓴다. 기사에 없는 값은 가정으로만: 연봉 2,800~3,500만 원 또는 월급 230~290만 원 사이 값, 아니면 딱 떨어지는 예시 금액(대출 1,000만 원 등).
- 가정을 쓰면 그 카드에 밝힌다("연봉 3,000만 원 기준", "대출 1,000만 원이면").
- 계산식은 "calculations"에 남긴다. 코드가 다시 계산해 보고 틀리면 다시 쓰게 한다.
  형식: {"slide": 결과가 나오는 장 번호(1부터), "inputs": {"a": {"value": "171만원", "source": "원문"}, "s": {"value": "1,000만 원", "source": "가정", "label": "대출"}},
         "formula": "a - b" (재료 이름과 + - * / 괄호, 12·100 같은 환산 수만), "result": "5만 원"}
- 계산할 숫자가 기사에 정말 없으면 "calculations": []로 비운다(억지로 만들지 않는다).

[슬라이드 종류]""").replace(
        '  "hashtags": ["{hashtag_example}", "..."],',
        '  "calculations": [ {"slide": 3, "inputs": {...}, "formula": "...", "result": "..."} ],\n  "hashtags": ["{hashtag_example}", "..."],')
# 경제 판정 칸(topic_type·wallet_impact)을 출력 예시에도 넣는다. 규칙에만 있고 예시에 없어서 모델이 칸을 빠뜨리면
# 지갑 영향 없음으로 처리돼 연말정산 기사까지 떨어졌다(2026-11-02 dry-run).
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("{verdict_fields}", (
    '\n  "topic_type": "제도/정책 | 금리/대출 | 생활 물가 | 경제 개념 | 제외",'
    '\n  "wallet_impact": "사회초년생 월급·대출·적금·세금·생활비에 주는 영향 한 문장, 없으면 null",') if CAPTION_RULES else "")
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("{channel_rules}", "".join(
    f"{i}. {rule}\n" for i, rule in enumerate(config.CHANNEL_CONFIG["writer_rules"], 16)
)).replace("{hashtag_example}", config.CHANNEL_CONFIG["hashtag_example"]).replace(
    "3~7장. 할 말이 3장이면 3장으로", f"{SLIDES_MIN}~{SLIDES_MAX}장. 할 말이 {SLIDES_MIN}장이면 {SLIDES_MIN}장으로"
).replace("인스타 캡션 본문(3~5문장, 해시태그 제외)", CAPTION_SPEC)
# 말투도 채널별. 청년 채널(디징)은 단톡방에서 정보 챙겨주는 친구의 다정한 반말(2026-10-30 운영자 확정). 기본은 해요체.
BANMAL = config.CHANNEL_CONFIG.get("speech") == "banmal"
if BANMAL:
    SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
        '8. 말투는 친근한 해요체("~예요", "~했어요"). 한 문장은 짧게. 기사체("~했다", "~로 나타났다") 금지.',
        f"8. {config.CHANNEL_CONFIG['tone_rule']}")

EDITOR_PROMPT = """너는 인스타그램 카드뉴스 편집장이다. 기자가 쓴 슬라이드 초안을, 휴대폰으로 넘겨 보는 독자가
한 장을 2초 안에 읽고 이해하도록 다듬는다. 사실과 숫자는 원문 그대로 두고, 원문에 없는 정보는 넣지 않는다.

아래 순서로 점검하고 고친다.
1. 구성: 표지(궁금증) → 근거(숫자) → 반전·한계 → 정리로 자연스럽게 이어지는가.
   제목만 이어 읽어도 이야기가 되는가. 힘없는 장, 앞 장과 겹치는 장은 합치거나 뺀다(3~7장 유지).
   한 장에 메시지는 하나만.
2. 문장 구조: 한 문장에 정보 하나. 문장은 35자 이내. 주어와 서술어를 가깝게.
   수식어를 앞에 길게 쌓지 않는다("과거 반감기 이후 12~18개월 안에 큰 폭 상승했으며" 같은 긴 연결문은 둘로 자른다).
   "~며", "~고," 로 문장을 이어 붙이지 않는다.
3. 어감: 모든 문장 끝을 해요체("~예요", "~해요", "~죠", "~어요")로 통일한다.
   합쇼체("~습니다"), 기사체("~했다", "~로 나타났다", "~것으로 보인다"), 번역투("~에 대해", "~에 있어서", "이같은"),
   과장("폭등", "폭락", "충격", "대박")을 없앤다. 친구에게 설명하듯 쉽고 담백하게.
4. 가독성: title·quote는 의미 단위로 "\\n" 줄바꿈해 한 줄 12자 이내. body는 문장 2개 이내.
   kicker는 짧은 명사구. 숫자는 단위와 붙여 쓴다(53.7%, 4년).

출력: 고친 덱 전체를 초안과 같은 JSON 형식으로, 다른 말 없이 출력한다.
"edits" 배열에 무엇을 왜 고쳤는지 짧게 적는다(예: "3장 긴 연결문을 두 문장으로 분리")."""

EDITOR_PROMPT = EDITOR_PROMPT.replace("(3~7장 유지)", f"({SLIDES_MIN}~{SLIDES_MAX}장 유지)")
if REQUIRE_CALC:
    EDITOR_PROMPT = EDITOR_PROMPT.replace("\n\n출력:", (
        "\n5. 금액 계산: 초안의 \"calculations\"는 그대로 두고, 장을 합치거나 빼서 번호가 바뀌면 slide 번호를 맞춘다. "
        "계산 결과 문구(예: \"월 5만 원\")와 가정 문구(\"연봉 3,000만 원 기준\")는 그 장에서 지우지 않는다.\n\n출력:"))
if BANMAL:
    EDITOR_PROMPT = EDITOR_PROMPT.replace(
        '3. 어감: 모든 문장 끝을 해요체("~예요", "~해요", "~죠", "~어요")로 통일한다.', f"3. 어감: {config.CHANNEL_CONFIG['editor_tone']}"
    ).replace("친구에게 설명하듯 쉽고 담백하게.", "단톡방에서 정보 챙겨주는 친구가 말하듯 짧고 정확하게.")
if CAPTION_CLOSING_WORDS:  # 편집장이 캡션을 다듬다 마지막 줄을 지운다(2026-10-30 인천 박람회 덱 3회 실패)
    EDITOR_PROMPT = EDITOR_PROMPT.replace("\n\n출력:", (
        "\n5. 캡션: 마지막 줄은 친구 태그·공유를 부탁하는 한마디로 끝낸다(예: \"같이 갈 친구 태그해\", "
        "\"이거 필요한 친구한테 보내줘\"). 신청 방법·주의할 점 줄 뒤에 둔다.\n\n출력:"))
if CAPTION_DATE_MARK:  # 원문에 날짜·기간이 있으면 캡션 기간 줄은 빼지 않는다(2026-10-30 한남대 덱 캡션에 기간 줄 없음)
    EDITOR_PROMPT = EDITOR_PROMPT.replace("\n\n출력:", (
        f"\n6. 캡션 기간 줄: 원문에 날짜·기간이 있으면 캡션의 \"{CAPTION_DATE_MARK}\" 줄을 지우지 말고 원문 표기 그대로 둔다.\n\n출력:"))
# 반말 채널은 끝맺음이 다양해 허용 목록 대신 기사체·합쇼체 끝맺음만 막는다.
_ARTICLE_END_RE = re.compile(r"(습니다|습니까|었다|았다|했다|였다|이다|한다|된다|있다|없다|보인다)[.!?…]*$")

LIMITS = {
    "title": 26, "kicker": 14, "subtitle": 34, "label": 32, "value": 12, "note": 56,
    "body": 96, "quote": 54, "source": 24,
}


def normalize_hanja(text: str) -> str:
    for hanja, hangul in HANJA_ABBREVIATIONS.items():
        text = text.replace(hanja, hangul)
    return text


def _texts(slide: dict) -> list[str]:
    texts = [v for k, v in slide.items() if isinstance(v, str) and k != "kind"]
    for item in slide.get("items") or []:
        texts.extend(item.values() if isinstance(item, dict) else [item])
    if isinstance(slide.get("hook"), list):  # 표지 말풍선·알림 문장(테크 채널 템플릿)
        texts.extend(slide["hook"])
    return [t for t in texts if isinstance(t, str)]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text)}


def _article_dates(article: str) -> set[tuple[int, int]]:
    return {(int(m), int(d)) for regex in _ARTICLE_DATE_RES for m, d in regex.findall(article)}


def check_facts(text: str, article: str, where: str, verified: list[float] | None = None) -> None:
    """날짜(월·일)와 한 자리 숫자가 붙은 금액·비율·나이·배수를 원문과 맞춘다(두 자리 이상 숫자는 기존 대조가 본다).
    verified: 검증된 계산 결과·가정 금액(check_calculations). 이 금액은 원문에 없어도 된다."""
    dates = _article_dates(article)
    for m, d in _DATE_RE.findall(text):
        if (int(m), int(d)) not in dates:
            raise ValueError(f"{where} 원문에 없는 날짜 {m}월 {d}일: {text!r}")
    original, text = text, strip_verified(text, verified or [])
    compact = re.sub(r"\s+", "", article)
    for num, unit in _UNIT_NUMBER_RE.findall(text):
        if f"{num}{unit}" not in compact:
            raise ValueError(f"{where} 원문에 없는 수치 {num}{unit}(원문 표기 그대로 쓰거나, 계산값이면 calculations에 남길 것): {original!r}")
    compact_text = re.sub(r"\s+", "", original)
    for word in CLAIM_WORDS:
        key = re.sub(r"\s+", "", word)
        if key in compact_text and key not in compact:
            raise ValueError(f"{where} 원문에 없는 내용 {word!r}(기사에 나온 것만 쓸 것): {text!r}")


def check_plan_notice(deck: dict, article: str) -> None:
    """원문에 아직 확정 전(정부안·입법예고·시행 예정 등)이라는 말이 있으면 카드나 캡션 어딘가에 그 표시가 있어야 한다."""
    words = [w for w in PLAN_WORDS if w in article]
    if not words:
        return
    texts = " ".join(t for s in deck.get("slides") or [] for t in _texts(s)) + " " + str(deck.get("caption") or "")
    if not any(m in texts for m in PLAN_MARKERS):
        raise ValueError(f"원문에 확정 전 표현({', '.join(words[:3])})이 있는데 카드·캡션에 '예정'·'정부안' 같은 표시가 없음")


def _guess_date(m: int, d: int, today: date) -> date:
    """연도 없는 월·일을 오늘에서 가까운 해로 푼다. 없는 날짜(2월 30일 등)면 ValueError."""
    guess = date(today.year, m, d)
    if (today - guess).days > 180:  # 연말에 쓴 "1월 15일"은 내년
        guess = date(today.year + 1, m, d)
    elif (guess - today).days > 180:  # 연초에 쓴 "12월 20일"은 작년
        guess = date(today.year - 1, m, d)
    return guess


def _resolve_day(prefix: str, d: int, ref: date) -> date:
    """월 없이 일만 쓴 날짜("29일")를 기사 발행일이 있는 달로 본다. "오는"은 발행일 뒤로, "지난"은 앞으로 맞춘다."""
    month_start = ref.replace(day=1)
    guess = month_start.replace(day=d)
    if prefix == "오는" and guess < ref:
        guess = (month_start + timedelta(days=32)).replace(day=d)
    elif prefix == "지난" and guess > ref:
        guess = (month_start - timedelta(days=1)).replace(day=d)
    return guess


def has_upcoming_date(article: str, published: date | None = None, today: date | None = None) -> bool:
    """원문에 오늘 이후 날짜("11월 2일", "오는 2일")가 하나라도 있으면 아직 안 끝난 모집·행사일 수 있다.
    작성 모델의 "ended" 판단은 시도마다 흔들려서(2026-10-30 접수 전인 용인 청년기본소득을 끝났다고 봄) 이걸로 받친다.
    "11.2" 같은 점 표기는 "1.5배"와 구분이 안 돼 여기서는 보지 않는다(건너뛰기를 막는 쪽으로 잘못 걸리므로)."""
    today = today or datetime.now(KST).date()
    for m, d in _DATE_RE.findall(article):
        try:
            if _guess_date(int(m), int(d), today) >= today:
                return True
        except ValueError:
            continue
    for prefix, d in _DAY_ONLY_RE.findall(article):
        try:
            if prefix == "오는" and _resolve_day(prefix, int(d), published or today) >= today:
                return True
        except ValueError:
            continue
    return False


def check_deadline(deck: dict, published: date | None = None, today: date | None = None) -> None:
    """카드·캡션의 행사일·마감일이 모두 오늘보다 앞이면 이미 끝난 모집·행사다. 다시 써도 소용없으니 기사를 건너뛴다.
    행사·마감 표지(EVENT_DATE_WORDS)가 있는 장(캡션은 줄)의 날짜만 보고, 일만 쓴 날짜는 기사 발행일 기준으로 푼다."""
    today = today or datetime.now(KST).date()
    units = [" ".join(_texts(s)) for s in deck.get("slides") or []] + str(deck.get("caption") or "").splitlines()
    found = []
    for unit in units:
        if not any(k in unit for k in EVENT_DATE_WORDS):
            continue
        for y, m, d in _YMD_RE.findall(unit):
            try:
                if y and int(y) < today.year:  # 생년월일·지난해 기록은 행사일이 아니다
                    continue
                found.append(date(int(y), int(m), int(d)) if y else _guess_date(int(m), int(d), today))
            except ValueError:
                continue
        for prefix, d in _DAY_ONLY_RE.findall(unit):
            try:
                found.append(_resolve_day(prefix, int(d), published or today))
            except ValueError:
                continue
    if found and max(found) < today:
        raise SkipArticle(f"행사일·마감이 이미 지남({max(found).isoformat()})이라 게시하지 않음")


def check_post_event(article: str, published: date | None = None, today: date | None = None) -> None:
    """원문이 이미 끝난 행사를 전하는 사후 보도("개최했다고 밝혔다", "전날 ~에서", "성료")면 모집·행사 정보로 쓰지 않는다.
    작성 모델의 "ended" 판단과 별도로 코드로 한 번 더 본다. 원문에 앞으로의 날짜가 있으면(사후 보도 끝에 다음 접수 안내 등) 넘긴다."""
    if not _POST_EVENT_RE:
        return
    m = _POST_EVENT_RE.search(article)
    if m and not has_upcoming_date(article, published, today):
        raise SkipArticle(f"이미 끝난 행사의 사후 보도({m.group(0)!r})라 게시하지 않음")


def host_names(host: str | None) -> list[str]:
    """공고 주최 표기("SBS A&T / (주)진심커뮤니케이션", "고용노동부 청년고용정책과")에서 캡션에 쓸 만한 기관명 후보.
    여러 주최는 각각, 부서까지 붙은 이름은 앞 낱말(기관명)도 인정한다."""
    names = []
    for part in re.split(r"\s*[/,·]\s*", host or ""):
        part = re.sub(r"\(주\)|\(재\)|㈜|주식회사|재단법인|사단법인", "", part).strip()
        if len(part) >= 2:
            names.append(part)
            first = part.split()[0]
            if first != part and len(first) >= 2:
                names.append(first)
    return names


def check_listing_text(text: str, article: str, host: str | None, where: str) -> None:
    """공고 카드 문구: 기관명이 잘려 다른 말과 붙은 경우("한국도로청년인턴")와 "·" 목록 속 줄임말("한부·의사")."""
    names = host_names(host)
    for name in (n for n in names if len(n) >= 5):
        for m in re.finditer(re.escape(name[:4]), text):
            if any(text.startswith(n, m.start()) for n in names):
                continue
            rest = text[m.start():]
            same = next(k for k in range(4, len(name) + 1) if k == len(name) or k == len(rest) or rest[k] != name[k])
            if same < len(name) and same < len(rest) and _HANGUL_RE.match(rest[same]):
                raise ValueError(f"{where} 기관명이 잘려 다른 말과 붙음({rest[:same + 4]!r}) — 공식 이름 {name!r} 그대로 쓸 것: {text!r}")
    for group in re.findall(r"[가-힣A-Za-z]+(?:\s*·\s*[가-힣A-Za-z]+)+", text):
        for word in re.split(r"\s*·\s*", group):
            full = LIST_ABBREVIATIONS.get(word)
            if full and full in article:
                raise ValueError(f"{where} 줄임말 {word!r}(원문 표기 {full!r}로 쓰거나 문장을 나눌 것): {text!r}")


def check_listing_caption(deck: dict, listing: dict) -> None:
    """공고 카드 캡션: 🗓 줄에 신청 기간과 D-n(코드가 마감일로 계산한 값), ✅ 신청 방법 줄에 공식 기관명."""
    lines = str(deck.get("caption") or "").splitlines()
    date_line = next((line for line in lines if CAPTION_DATE_MARK and CAPTION_DATE_MARK in line), None)
    d_mark = f"D-{listing['days_left']}"
    if not date_line:
        raise ValueError(f"캡션에 {CAPTION_DATE_MARK} 신청 기간 줄이 없음(신청 기간과 {d_mark}를 넣을 것)")
    if d_mark not in date_line.replace(" ", ""):
        raise ValueError(f"캡션 {CAPTION_DATE_MARK} 줄에 {d_mark}가 없음(공고 정보의 값 그대로): {date_line!r}")
    how_line = next((line for line in lines if "✅" in line), None)
    names = host_names(listing.get("host"))
    if names and not (how_line and any(n in how_line for n in names)):
        raise ValueError(f"캡션 ✅ 신청 방법 줄에 공식 기관명({names[0]})이 없음: {how_line!r}")


def check_deck(deck: dict, article: str, published: date | None = None, listing: dict | None = None,
               title: str = "", outlet: str | None = None, v2: bool = False) -> None:
    """v2(사실표 작성, 2026-11-02): 칸 상한은 V2_LIMITS(장당 60자·3줄은 check_v2), 짧은 명사구 줄을 허용해 문장 끝맺음
    검사는 tone_errors(합쇼체·기사체만)로 대신하고, 기사·기관이 밝힌 전망 인용과 계산에 쓴 숫자의 반복을 허용한다."""
    limits = V2_LIMITS if v2 else LIMITS
    slides = deck.get("slides") or []
    if not SLIDES_MIN <= len(slides) <= SLIDES_MAX:
        raise ValueError(f"슬라이드 수 {len(slides)}장 ({SLIDES_MIN}~{SLIDES_MAX}장이어야 함)")
    if slides[0].get("kind") != "cover":
        raise ValueError("첫 장이 cover가 아님")
    # 건너뛸 기사면 다른 검사로 편집을 되풀이하기 전에 먼저 끊는다(뉴런 절약).
    # 공고는 마감일(apply_end)로 이미 결정적으로 걸렀으니 카드 문구 속 날짜로 다시 판단하지 않는다.
    if CHECK_DEADLINE and not listing:
        check_deadline(deck, published)
    subtitle = slides[0].get("subtitle")
    if isinstance(subtitle, str) and subtitle.strip() and not _BARE_SUBTITLE_RE.sub("", subtitle):
        raise ValueError(f"표지 subtitle이 숫자·날짜뿐(무엇인지 말로 쓸 것): {subtitle!r}")
    known = {"cover", "stat", "compare", "point", "list", "quote", "closing"}
    verified = check_calculations(deck, article)
    # 계산식에 실제로 쓴 환산 수(12개월·100 등)는 카드에 "× 12개월"처럼 써도 된다
    formulas = " ".join(str(c.get("formula") or "") for c in deck.get("calculations") or [] if isinstance(c, dict))
    article_numbers = _numbers(article) | {str(n) for n in _CALC_CONSTANTS if re.search(rf"(?<![\d.]){n}(?![\d.])", formulas)}
    seen: list[str] = []
    number_slides: dict[str, set[int]] = {}
    for i, slide in enumerate(slides, 1):
        if slide.get("kind") not in known:
            raise ValueError(f"{i}장: 알 수 없는 종류 {slide.get('kind')!r}")
        for key in limits:
            if key in slide and slide[key] is not None and not isinstance(slide[key], str):
                raise ValueError(f"{i}장 {key}는 문자열이어야 함(목록·객체 금지): {slide[key]!r}")
        items = slide.get("items")
        if items is not None and not (isinstance(items, list) and all(isinstance(x, (str, dict)) for x in items)):
            raise ValueError(f"{i}장 items 형식 오류: {items!r}")
        hook = slide.get("hook")
        if hook is not None and not (isinstance(hook, list) and len(hook) <= 3
                                     and all(isinstance(h, str) and len(h) <= HOOK_MAX for h in hook)):
            raise ValueError(f"{i}장 hook은 {HOOK_MAX}자 이내 문장 3개 이하의 목록이어야 함: {hook!r}")
        for key, limit in limits.items():
            if isinstance(slide.get(key), str) and len(slide[key]) > limit:
                raise ValueError(f"{i}장 {key} 길이 초과({len(slide[key])}>{limit}): {slide[key]!r}")
        if v2:  # 렌더에서 넘친 것(11/2 전세대출 덱): 비교 항목 4개는 제목과 겹치고, 긴 숫자 값은 글자 블록이 세 줄로 넘어간다
            if slide.get("kind") == "compare" and len(slide.get("items") or []) > 3:
                raise ValueError(f"{i}장 compare 항목이 {len(slide['items'])}개(2~3개만, 나머지는 note나 다른 장으로)")
            if slide.get("kind") == "stat" and len(str(slide.get("value") or "")) > 8:
                raise ValueError(f"{i}장 stat value가 너무 김({slide['value']!r}, 8자 이내 숫자 하나. 범위는 label·note로)")
            for item in slide.get("items") or []:
                text = " ".join(item.values()) if isinstance(item, dict) else str(item)
                if len(text) > V2_ITEM_MAX:
                    raise ValueError(f"{i}장 목록 항목 길이 초과({len(text)}>{V2_ITEM_MAX}): {text!r}")
        for key in ("title", "quote"):
            for line in (slide.get(key) or "").split("\n"):
                if len(line.strip()) > TITLE_LINE_MAX:
                    raise ValueError(f"{i}장 {key} 한 줄이 {TITLE_LINE_MAX}자 초과(의미 단위로 \\n 줄바꿈 필요): {line!r}")
        for key in SENTENCE_FIELDS if not v2 else ():
            for sentence in _SENTENCE_SPLIT_RE.split((slide.get(key) or "").strip()):
                if not sentence:
                    continue
                if len(sentence) > SENTENCE_MAX:
                    raise ValueError(f"{i}장 {key} 문장이 {SENTENCE_MAX}자 초과: {sentence!r}")
                if BANMAL and _ARTICLE_END_RE.search(sentence):
                    raise ValueError(f"{i}장 {key} 기사체·합쇼체 문장(반말로): {sentence!r}")
                if not BANMAL and not _POLITE_END_RE.search(sentence):
                    raise ValueError(f"{i}장 {key} 해요체가 아닌 문장: {sentence!r}")
        for text in _texts(slide):
            banned = next((b for b in BANNED_PHRASES if b in text
                           and not (v2 and b in FORECAST_PHRASES and _ATTRIBUTION_RE.search(text))), None)
            if banned:
                raise ValueError(f"{i}장 금지 표현 {banned!r}: {text!r}")
            if _STRAY_SCRIPT_RE.search(text):
                raise ValueError(f"{i}장 한자/일본어 혼입: {text!r}")
            if NO_EMOJI_ON_CARDS and _EMOJI_RE.search(text):
                raise ValueError(f"{i}장 카드 문구에 이모지(이 채널은 카드에 이모지 금지, 캡션에만): {text!r}")
            check_spelling(text, f"{i}장")
            check_outlet(text, outlet, f"{i}장")
            if listing:
                check_listing_text(text, article, listing.get("host"), f"{i}장")
            check_facts(text, article, f"{i}장", verified)
            # 원문에 없는 숫자 금지(연도·퍼센트·금액 모두). 한 자리 수는 서수·개수라 허용. 검증된 계산 금액은 뺀다.
            for n in _numbers(strip_verified(text, verified)):
                if len(n.replace(".", "")) >= 2 and n not in article_numbers:
                    hint = "(계산 결과·가정 금액이면 calculations에 계산식과 source \"가정\"으로 남길 것)" if REQUIRE_CALC else ""
                    raise ValueError(f"{i}장 원문에 없는 숫자 {n!r}{hint}: {text!r}")
        combined = " ".join(_texts(slide))
        for n in _numbers(combined):
            number_slides.setdefault(n, set()).add(i)
        if any(fuzz.token_sort_ratio(combined, prev) >= 80 for prev in seen):
            raise ValueError(f"{i}장 내용이 앞 장과 거의 같음")
        seen.append(combined)
    calc_numbers = calculation_numbers(deck) if v2 else set()  # 계산 재료·결과 숫자는 장마다 다시 나와도 된다
    overused = [n for n, idx in number_slides.items()
                if len(n.replace(".", "")) >= 2 and len(idx) > 2 and n not in calc_numbers]
    if overused:
        raise ValueError(f"같은 숫자를 3장 이상에서 반복: {overused}")
    if slides[-1].get("kind") == "closing" and fuzz.token_sort_ratio(
        slides[-1].get("title", ""), slides[0].get("title", "")
    ) >= 70:
        raise ValueError("마지막 장이 표지 제목을 되풀이함")
    if CHECK_CAPTION:
        check_caption(strip_verified(str(deck.get("caption") or ""), verified), article_numbers)  # 검증된 계산 금액은 캡션에도
        check_facts(factual_caption(deck["caption"]), article, "캡션", verified)
    check_spelling(str(deck.get("caption") or ""), "캡션")
    if CAPTION_RULES:
        check_caption_rules(str(deck.get("caption") or ""), title, outlet)
    if CAPTION_CLOSING_WORDS:
        lines = [line for line in str(deck.get("caption") or "").splitlines() if line.strip()]
        last = lines[-1] if lines else ""
        if not any(w in last for w in CAPTION_CLOSING_WORDS):
            raise ValueError(f"캡션 마지막 줄이 친구 태그·공유 부탁이 아님({'/'.join(CAPTION_CLOSING_WORDS)} 중 하나를 넣을 것): {last!r}")
    if CAPTION_DATE_MARK and _PERIOD_RE.search(article) and CAPTION_DATE_MARK not in str(deck.get("caption") or ""):
        raise ValueError(f"원문에 날짜·기간이 있는데 캡션에 {CAPTION_DATE_MARK} 기간 줄이 없음(원문 표기 그대로 넣을 것)")
    if listing:
        check_listing_caption(deck, listing)
        check_listing_text(factual_caption(str(deck.get("caption") or "")), article, listing.get("host"), "캡션")
        # 11/2 디징 170142: 표지가 "청년인턴 116명 모집"뿐이라 어느 기관 인턴인지 몰랐다(v2는 check_v2가 본다)
        names = host_names(listing.get("host"))
        if not v2 and names and not any(_compact(n) in _compact(str(slides[0].get("title") or "")) for n in names):
            raise ValueError(f"공고 표지 제목에 기관 이름({display_host(listing.get('host'))})이 없음"
                             f"(첫 줄에 줄이지 말고 그대로): {slides[0].get('title')!r}")
    check_plan_notice(deck, article)


def check_caption(caption, article_numbers: set[str]) -> None:
    """자세한 설명을 캡션에 담는 채널은 대상·금액·기간이 캡션에 들어가므로 슬라이드와 같은 기준으로 본다."""
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError("캡션이 비어 있음")
    if len(caption) > CAPTION_MAX:
        raise ValueError(f"캡션 {len(caption)}자 ({CAPTION_MAX}자 이내여야 함)")
    banned = next((b for b in BANNED_PHRASES if b in caption), None)
    if banned:
        raise ValueError(f"캡션 금지 표현 {banned!r}")
    if _STRAY_SCRIPT_RE.search(caption):
        raise ValueError("캡션에 한자/일본어 혼입")
    made_up = sorted(n for n in _numbers(factual_caption(caption)) if len(n.replace(".", "")) >= 2 and n not in article_numbers)
    if made_up:
        raise ValueError(f"캡션에 원문에 없는 숫자 {made_up}")


def factual_caption(caption: str) -> str:
    """원문 대조 대상 캡션. 계산 예시 줄(calc_line_prefix, 경제 "예)")은 원문에 없는 계산값이 들어가므로 뺀다."""
    if not CALC_PREFIX:
        return caption
    return "\n".join(line for line in caption.splitlines() if not line.strip().startswith(CALC_PREFIX))


def strip_outlet(title: str) -> str:
    """"코스피 소폭 하락 - 생생비즈플러스" → "코스피 소폭 하락"."""
    return _OUTLET_TAIL_RE.sub("", title or "").strip()


def normalize_title(title: str) -> str:
    """중복·유사도 비교용: 매체 꼬리표·괄호 머리말·문장부호를 빼고 한자 약어를 푼다."""
    text = normalize_hanja(strip_outlet(title))
    text = re.sub(r"\[[^\]]*\]|【[^】]*】|<[^>]*>", " ", text)
    text = re.sub(r"[^\w%.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def check_spelling(text: str, where: str) -> None:
    m = _SPELLING_RE.search(text) or (CAPTION_RULES and _REPEAT_WORD_RE.search(text))
    if m:
        raise ValueError(f"{where} 어색한 어미·중복 조사 {m.group(0)!r}(맞춤법 확인): {text!r}")


def check_outlet(text: str, outlet: str | None, where: str) -> None:
    """기사 매체 이름이 카드·캡션에 남았는지. 캡션 "참고:" 줄은 발표 기관·보도 표기라 호출 측에서 뺀다."""
    name = (outlet or "").strip()
    if len(name) >= 2 and name in text:
        raise ValueError(f"{where}에 매체 이름 {name!r}(매체명 금지, 출처는 캡션 '참고:' 줄에 기관으로): {text!r}")


def check_caption_rules(caption: str, title: str, outlet: str | None) -> None:
    """경제 캡션 구조: 자체 훅(검색 키워드, 기사 제목 아님) → • 요약 3줄 → "그래서 내 ~은?" 지갑 영향 → "참고: 기관 발표/보도"."""
    lines = [line.strip() for line in caption.splitlines() if line.strip()]
    if len(lines) < 6:
        raise ValueError(f"캡션이 {len(lines)}줄(훅·요약 3줄·지갑 영향·참고 줄 구조여야 함)")
    for line in lines:
        if not line.startswith("참고:") and _CAPTION_TAIL_RE.search(line):
            raise ValueError(f"캡션 줄 끝에 ' - 매체명'·'| 매체명' 같은 꼬리표: {line!r}")
        if not line.startswith("참고:"):
            check_outlet(line, outlet, "캡션")
    hook, plain_title = lines[0], normalize_title(title)
    if hook.startswith("•") or hook.startswith("참고:"):
        raise ValueError(f"캡션 첫 줄이 훅 문장이 아님: {hook!r}")
    if plain_title and (plain_title in normalize_title(hook) or fuzz.ratio(normalize_title(hook), plain_title) >= 70):
        raise ValueError(f"캡션 첫 줄이 기사 제목과 거의 같음(자체 훅 문장으로 새로 쓸 것): {hook!r}")
    compact_hook = re.sub(r"\s+", "", hook)
    if HOOK_KEYWORDS and not any(re.sub(r"\s+", "", k) in compact_hook for k in HOOK_KEYWORDS):
        raise ValueError(f"캡션 첫 줄에 검색 키워드(예: {', '.join(HOOK_KEYWORDS[:5])})가 없음: {hook!r}")
    bullets = [line for line in lines if line.startswith("•")]
    if len(bullets) != 3:
        raise ValueError(f"캡션 요약이 {len(bullets)}줄(• 로 시작하는 3줄이어야 함)")
    if not any("그래서" in line and any(w in line for w in WALLET_WORDS) for line in lines):
        raise ValueError("캡션에 \"그래서 내 월급/대출/적금/세금은?\" 지갑 영향 줄이 없음")
    if not (lines[-1].startswith("참고:") and re.search(r"(발표|보도)[.)\s]*$", lines[-1])):
        raise ValueError(f"캡션 마지막 줄이 \"참고: 기관명 발표/보도\"가 아님: {lines[-1]!r}")


def clean_hashtags(raw) -> list[str]:
    """해시태그 3~5개(채널 hashtag_rules, 기본 상한 5), 한글·영문·숫자만. 범용 금지 태그(#경제 #카드뉴스 등)는 빼고 센다."""
    banned = {b.lower() for b in HASHTAG_RULES["banned"]}
    tags: list[str] = []
    for tag in raw if isinstance(raw, list) else []:
        tag = str(tag).strip().lstrip("#").replace(" ", "")
        if not tag or tag.lower() in banned or tag in tags:
            continue
        if not _HASHTAG_RE.fullmatch(tag):
            raise ValueError(f"해시태그 #{tag}에 한글·영문·숫자 외 문자(한자·가나 등 금지)")
        tags.append(tag)
    tags = tags[: HASHTAG_RULES["max"]]
    if len(tags) < HASHTAG_RULES["min"]:
        raise ValueError(f"쓸 수 있는 해시태그가 {len(tags)}개({HASHTAG_RULES['min']}~{HASHTAG_RULES['max']}개 필요, "
                         f"범용 태그 {', '.join('#' + b for b in HASHTAG_RULES['banned'][:4])} 등은 제외됨)")
    return tags


# 템플릿 장식 문구(창 제목·버튼·필기체 등). 매 게시물이 같은 문구를 되풀이하지 않게 덱마다 모델이 새로 짓는다
# (2026-10-30 운영자: "세부 내용은 컨셉에 맞춰 항상 바뀌어야"). 키는 card_templates/README.md 참고.
PROP_TEXT = {"script": 22, "closing_script": 22, "window_title": 10, "memo_title": 8, "label": 8, "ask": 16}
PROP_PAIRS = {"buttons": 6, "closing_buttons": 6, "folders": 8}
COVER_TILES = config.CHANNEL_CONFIG.get("cover_tiles", False)  # 경제는 끔 → 표지 블록은 브랜드 워드마크 그대로
COVER_TILE_ROWS, COVER_TILE_CHARS = 2, 5
_SCRIPT_RE = re.compile(r"^[a-z][a-z' .,&!?-]*$")  # 영문 필기체 글꼴(Allura)은 한글이 없다


def clean_props(props, article: str, host: str | None = None) -> dict:
    """틀린 항목은 덱을 다시 쓰게 하지 않고 버린다(템플릿 기본값이 대신 들어감). 장식 문구 하나 때문에
    무료 한도 안에서 덱 전체를 재생성할 가치는 없다. 숫자·금지 표현·한자 기준은 슬라이드와 같다."""
    if not isinstance(props, dict):
        return {}
    article_numbers = _numbers(article)

    def ok(text, limit: int) -> bool:
        return (isinstance(text, str) and 0 < len(text.strip()) <= limit
                and not _STRAY_SCRIPT_RE.search(text) and not any(b in text for b in BANNED_PHRASES)
                and all(len(n.replace(".", "")) < 2 or n in article_numbers for n in _numbers(text)))

    out = {}
    for key, limit in PROP_TEXT.items():
        value = props.get(key)
        if ok(value, limit):
            value = value.strip().lower() if key.endswith("script") else value.strip()
            if not key.endswith("script") or _SCRIPT_RE.match(value):
                out[key] = value
    for key, limit in PROP_PAIRS.items():
        value = props.get(key)
        if isinstance(value, list) and len(value) == 2 and all(ok(v, limit) for v in value):
            out[key] = [v.strip() for v in value]
    # 표지 글자 블록 주제 문구(blocktitle 템플릿, 2026-11-01). 켠 채널(디징)만. 최대 2줄, 줄당 5자, 띄어쓰기는 뺀다
    tiles = props.get("cover_tiles")
    if COVER_TILES and isinstance(tiles, str):
        rows = [re.sub(r"\s", "", r) for r in tiles.replace("\\n", "\n").split("\n")]
        rows = [r for r in rows if r]
        # 공고 기관명은 공식 이름에서 이어진 글자만("도로공단"은 한국도로공사로 읽힌다 → "도로교통"), 11/2 디징 170142
        names = [_compact(n) for n in host_names(host)]
        if (1 <= len(rows) <= COVER_TILE_ROWS and all(ok(r, COVER_TILE_CHARS) for r in rows)
                and (not names or any(rows[0] in n for n in names))):
            out["cover_tiles"] = "\n".join(rows)
    return out


def clean_article(text: str) -> str:
    return normalize_hanja(_BOILERPLATE_RE.sub("", text)).strip()


def _parse(raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)  # 추론 블록을 본문에 붙여 주는 모델(Qwen3 등)
    # 특수 하이픈(‑ U+2011 등)을 보통 "-"로. 11/2 디징 170142: "D‑7"이 "D-7" 검사에 세 번 걸렸다
    raw = raw.translate({0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2212: "-"})
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def _raise_if_quota_exhausted(e: Exception) -> None:
    """Cloudflare 429는 바로 포기한다. 하루 한도 소진(응답 본문으로 판별)이면 재시도해도 소용없고, 일시 제한이면
    llm_client가 이미 30초→2분→5분 기다리고 대체 모델까지 불러 본 뒤다(2026-11-02 구분)."""
    import llm_client

    if isinstance(e, (llm_client.DailyQuotaExceeded, llm_client.TemporaryLimit)):
        raise e
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
        raise RuntimeError(f"Cloudflare 429(원인 미확인): {e.response.text[:200]}") from e


def display_host(host: str | None) -> str:
    """캡션·고지에 쓸 기관명. "고용노동부 청년고용정책과"처럼 부서까지 붙어 길면 앞 낱말만."""
    names = host_names(host)
    if not names:
        return host or "주최 기관"
    return names[0] if len(names[0]) <= 15 or len(names) == 1 else names[1]


def listing_disclaimer(host: str | None) -> str:
    return config.CHANNEL_CONFIG.get("listing_disclaimer", DISCLAIMER).replace("{host}", display_host(host))


def write_deck(title: str, article: str, category: str, hint: str = "", published: date | None = None,
               listing: dict | None = None, outlet: str | None = None) -> dict:
    """초안 작성 → 편집장 검수(구성·문장 구조·어감·가독성) → 코드 검사.
    검사에 걸리면 걸린 이유를 편집 단계에 돌려줘 고치게 하고, 그래도 안 되면 초안부터 다시 쓴다.
    hint: 지난번 렌더에서 걸린 문제(글자 넘침 등). 다시 쓸 때 피하도록 원문 뒤에 붙인다.
    published: 기사 발행일(KST). "29일"처럼 일만 쓴 날짜를 푸는 기준이고, 없으면 오늘.
    listing: 공식 모집 공고면 {"host": 주최, "days_left": 마감까지 남은 날}(디징, 2026-10-31). 마감은 호출 측이
    apply_end로 이미 걸렀으므로 기사용 검사(사후 보도·"ended" 판단·카드 문구 속 날짜)는 건너뛴다.
    outlet: 기사 매체 이름. 카드·캡션에 남으면 실패(제목 끝 " - 매체명"은 모델에 넘기기 전에 뗀다)."""
    if WRITER_V2:
        return write_deck_v2(title, article, category, hint=hint, published=published, listing=listing, outlet=outlet)
    title = strip_outlet(title)
    if listing and listing.get("host") and display_host(listing["host"]) not in title:  # 잡알리오 제목엔 기관명이 없다
        title = f"{display_host(listing['host'])} {title}"
    article = clean_article(article)
    if CHECK_DEADLINE and not listing:  # 사후 보도는 모델을 부르기 전에 거른다(뉴런 절약)
        check_post_event(article, published)
    label = "공고 원문" if listing else "기사 원문"
    source = normalize_hanja(f"카테고리: {config.CATEGORY_LABELS.get(category, category)}\n제목: {title}\n\n[{label}]\n{article}")
    if listing:
        source += (
            "\n\n[공고 카드 규칙] 위 원문은 뉴스 기사가 아니라 지금 신청할 수 있는 공식 모집 공고다. 원문의 \"기사\"는 이 공고를 말한다. "
            "덱의 \"ended\"는 false. "
            f"캡션 {CAPTION_DATE_MARK} 줄에 신청 기간과 \"D-{listing['days_left']}\"를 그대로 쓴다"
            f"(예: \"{CAPTION_DATE_MARK} ~11월 12일까지 (D-{listing['days_left']})\", 날짜는 원문 표기). "
            f"캡션 ✅ 신청 방법 줄에 공식 기관명 \"{display_host(listing.get('host'))}\"을 쓴다. "
            f"표지 title 첫 줄은 기관명 \"{display_host(listing.get('host'))}\"을 줄이지 않고 그대로, 둘째 줄에 모집 종류를 쓴다"
            f"(예: \"{display_host(listing.get('host'))}\\n청년인턴 모집\"). 카드만 봐도 어느 기관의 무슨 모집인지 바로 보이게."
        )
    if hint:
        source += f"\n\n[지난번 카드 렌더에서 걸린 문제 — 이번엔 피할 것]\n{hint}"
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            draft = _parse(call_text_model(SYSTEM_PROMPT, source, max_tokens=8000, model=MODEL))
        except Exception as e:  # 파싱 실패, 응답 잘림(incomplete), 일시적 API 오류 모두 다음 시도로
            _raise_if_quota_exhausted(e)
            last_error = e
            print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 초안 파싱 실패: {e}", file=sys.stderr)
            continue
        # 지난 행사 결과 기사 등. 다시 써도 소용없다. 원문에 앞으로의 날짜가 있으면 모델 판단만으로는 건너뛰지 않는다
        if CHECK_DEADLINE and not listing and draft.get("ended") is True and not has_upcoming_date(article, published):
            raise SkipArticle("이미 끝난 행사·모집이라 게시하지 않음(작성 단계 판단)")
        if CAPTION_RULES:  # 타깃 밖 주제·지갑 영향 없는 기사는 편집 단계로 넘기기 전에 끊는다(뉴런 절약)
            if str(draft.get("topic_type") or "").strip() == "제외":
                raise SkipArticle("주제 필터: 종목·시황·인사·행사·환율 트레이딩·정치 발언 기사(topic_type=제외)라 게시하지 않음")
            if "wallet_impact" not in draft:  # 칸을 빠뜨린 건 판정이 아니라 형식 실수 → 초안을 다시 쓴다
                last_error = ValueError("초안에 wallet_impact 칸이 없음")
                print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 초안에 wallet_impact 칸 없음", file=sys.stderr)
                continue
            if not str(draft.get("wallet_impact") or "").strip():
                raise SkipArticle("독자 지갑 영향(월급·대출·적금·세금 등)을 말할 수 없는 기사라 게시하지 않음")
        deck, feedback, no_calc = draft, "", 0
        for review in range(1, 4):  # 편집 1회 + 검사 실패 시 수정 최대 2회
            request = f"{source}\n\n[슬라이드 초안]\n{json.dumps(deck, ensure_ascii=False)}"
            if feedback:
                request += f"\n\n[검사에서 걸린 문제 — 반드시 고칠 것]\n{feedback}"
            try:
                deck = _parse(call_text_model(EDITOR_PROMPT, request, max_tokens=8000, model=MODEL))
                if REQUIRE_CALC and not deck.get("calculations") and draft.get("calculations"):
                    deck["calculations"] = draft["calculations"]  # 편집장이 계산 칸을 빠뜨리면 초안 것으로
                check_deck(deck, article + " " + title, published, listing, title=title, outlet=outlet)
                tags = " ".join("#" + t for t in clean_hashtags(deck.get("hashtags")))
                disclaimer = listing_disclaimer(listing.get("host")) if listing else DISCLAIMER
                footer = "\n".join(p for p in (disclaimer, tags) if p)
                final_caption = f"{str(deck.get('caption') or '').strip()}\n\n{footer}".strip()
                final_caption = comment_dm.add_line(final_caption)  # 댓글 자료 안내 1줄(허브 URL이 있을 때만), 2200자 검사에 포함
                if len(final_caption) > CAPTION_HARD_MAX:
                    raise ValueError(f"캡션이 고지·해시태그 포함 {len(final_caption)}자(인스타 한도 {CAPTION_HARD_MAX}자) — 본문을 줄일 것")
            except SkipArticle:
                raise
            except NoCalculation as e:  # 한 번 다시 쓰게 하고, 그래도 없으면 다음 순위 기사로
                no_calc += 1
                if no_calc >= 2:
                    raise SkipArticle(f"금액 계산 불가: {e}") from e
                last_error, feedback = e, str(e)
                print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 편집 {review}회차: {e}", file=sys.stderr)
                continue
            except Exception as e:
                _raise_if_quota_exhausted(e)
                last_error, feedback = e, str(e)
                print(f"[slide_writer] 시도 {attempt}/{MAX_ATTEMPTS} 편집 {review}회차 실패: {e}", file=sys.stderr)
                continue
            deck["caption"] = final_caption
            # 예시 문구를 베낀 값은 버린다 → 없으면 기사 제목으로 배경을 만든다(2026-10-30 주제와 무관한 책상 사진)
            deck["image_prompt"] = next((p for p in (deck.get("image_prompt"), draft.get("image_prompt"))
                                         if isinstance(p, str) and p.strip() and not p.startswith(_IMAGE_PROMPT_EXAMPLE)), None)
            deck["props"] = clean_props(deck.get("props") or draft.get("props"), article + " " + title,
                                        listing.get("host") if listing else None)
            return deck
    raise RuntimeError(f"슬라이드 생성 {MAX_ATTEMPTS}회 실패: {last_error}")


# =====================================================================================================
# v2 작성: 사실표 → 카드 (2026-11-02 카드 품질 개선, output/_quality_test/진단_카드품질.md)
# 진단: "궁금증 제목", "과감히 버린다", 짧은 글자 수, 교훈형 마무리가 겹쳐 제목에 주어·숫자가 없고 핵심 사실(대상·조건·
# 숫자·행동)이 빠졌다. 코드는 틀린 것만 막고 비어 있는 것은 막지 않았다.
# 1단계에서 원문 근거 문장이 붙은 사실표를 뽑고(코드가 근거 문장을 원문과 대조), 2단계는 그 사실표로만 카드를 쓴다.
# 장마다 사실표 항목을 담았는지, 제목에 핵심 명사가 있는지, 빈말 장이 없는지, 마지막 장이 구체적 행동인지 코드로 본다.
# 편집 단계는 따로 두지 않고(작성 규칙에 합침) 검사에 걸릴 때만 고쳐 쓰게 해서 호출 수를 줄인다.
# 카드 구조(같은 날 운영자): 표지 후보 10개 → 코드 조건 검사 → 편집 단계 1개 선택, 장 역할(경제 6장·디징 4~5장),
# 표지 외 장 60자·3줄·머리글 12자, 장마다 **강조** 1~2개, 장별 layout(렌더러가 반영), 사실 배치 기록(카드·캡션·가이드),
# 렌더 뒤 디자이너 검수 1회(generate_cardset이 부름).
# channel.json "writer_v2": true 또는 환경변수 SLIDE_WRITER_V2=1일 때만 쓴다.
# =====================================================================================================
WRITER_V2 = os.environ.get("SLIDE_WRITER_V2", "") == "1" or bool(config.CHANNEL_CONFIG.get("writer_v2"))
FACTS_MODEL = os.environ.get("FACTS_MODEL", MODEL)
# 칸 길이 상한(강조 표시 ** 포함). 본문은 장당 60자·3줄(check_v2)이 실제 기준이고 여기는 칸 하나의 안전 상한이다.
# 11/2 처음 정한 "본문 150자까지 완화"는 운영자가 취소(장당 60자로 압축, 넘치는 사실은 캡션·가이드로).
V2_LIMITS = {**LIMITS, "body": 90, "note": 70, "label": 70, "title": 40, "subtitle": 40}
V2_ITEM_MAX = 40
FORECAST_PHRASES = ("오를 가능성", "기대해 볼")  # 누가 한 전망인지 밝히면(인용) v2에서 허용
_ATTRIBUTION_RE = re.compile(r"(전망했|내다봤|예상했|밝혔|봤어|분석했|따르면|전망이에요|에 따르면)")
# 정보 없는 문장(빈말). 숫자·고유명사·조건·행동이 하나도 없는 장을 막는 검사와 별도로, 자주 나오는 꼴을 바로 잡는다.
_VAGUE_RE = re.compile(r"꼼꼼히 (확인|살펴|챙겨)|관심을? 가져|알아두면 좋|고민해(야| 보| 봐)|생각해 (보|봐)|"
                       r"참고하세요|놓치지 마세요|도움이 될 거|기억해 (두|주)|중요하다는 것|사례예요|사례입니다|더 알아보")
_CONDITION_RE = re.compile(r"이하|이상|미만|초과|부터|까지|대상|경우|조건|기준|만 \d|\d+세|해당|제외|면 ")
_ACTION_RE = re.compile(r"신청|확인|조회|비교|접속|가입|납부|등록|제출|전환|문의|검색|지원해|내자|넣어")
_KEY_WORD_RE = re.compile(r"[가-힣A-Za-z]{2,}")
_PARTICLE_RE = re.compile(r"(은|는|이|가|을|를|의|도|에|에서|으로|로|와|과|만|까지|부터|이나|나|이랑|랑|께|에게)$")
_TITLE_STOPWORDS = {"오늘", "올해", "내년", "지난해", "이번", "발표", "전망", "이유", "정부", "한국", "국내", "기록", "시대",
                    "역대", "최대", "최고", "급증", "증가", "감소", "상승", "하락", "비상", "단독", "분석", "종합", "속보",
                    "채용공고", "모집", "공고", "채용", "청년", "모두", "우리", "이것", "이거"}


def calculation_numbers(deck: dict) -> set[str]:
    """계산 재료·결과에 나온 숫자(표기의 숫자 부분). 같은 숫자 반복 제한에서 뺀다."""
    found: set[str] = set()
    for calc in deck.get("calculations") or []:
        if not isinstance(calc, dict):
            continue
        for spec in (calc.get("inputs") or {}).values():
            found |= _numbers(str(spec.get("value") if isinstance(spec, dict) else spec))
        found |= _numbers(str(calc.get("result") or ""))
    return found


def _stems(text: str) -> set[str]:
    """명사 비교용: 2자 이상 낱말에서 끝 조사를 뗀다("국민연금은" → "국민연금")."""
    words = set()
    for w in _KEY_WORD_RE.findall(text):
        stem = _PARTICLE_RE.sub("", w) if len(w) > 2 else w
        if len(stem) >= 2 and stem not in _TITLE_STOPWORDS:
            words.add(stem)
    return words


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


FACTS_PROMPT = """너는 카드뉴스 팀의 사실 정리 담당이다. 독자: {reader}
아래 원문에서 이 독자에게 필요한 핵심 사실을 5~10개 뽑아 JSON 하나로만 출력한다. 다른 말 금지.

뽑는 순서(앞일수록 중요, 원문에 있는 것만):
1. 무엇이 바뀌나·무슨 일인가 2. 얼마(금액·비율·인원·한도) 3. 누구에게 해당하나(나이·소득·조건) 4. 언제부터·언제까지
5. 독자가 할 일(어디서 무엇을 신청·확인하나) 6. 단서·예외·반론 7. 누가 한 전망인가(기관·전문가 이름과 함께)

각 사실:
- "id": "F1", "F2" …
- "fact": 사실 한 문장. 숫자·대상·조건을 빼지 말고 원문 표기 그대로.
- "numbers": 그 사실 속 숫자 표기 목록(원문 표기 그대로, 예: ["66%", "60만원"]). 없으면 [].
- "who": 누구에게 해당하는지(없으면 null), "when": 시점·기간(없으면 null)
- "evidence": 그 사실의 근거가 되는 원문 문장을 한 글자도 바꾸지 말고 그대로 복사한다. 코드가 원문과 대조한다.

최상위 칸:
- "core": 이 기사의 핵심 한 문장
- "title_terms": 기사 핵심 명사 2~4개(원문 표기, 예: ["국민연금", "수급자"])
- "action": 원문에 독자가 할 일이 있으면 {{"what": "무엇을", "where": "어디서(기관·사이트·앱 이름)"}}, 없으면 null
{extra_fields}
출력 예:
{{"core": "...", "title_terms": ["..."], "action": null,{extra_example} "facts": [{{"id": "F1", "fact": "...", "numbers": ["..."], "who": null, "when": null, "evidence": "..."}}]}}"""

FACTS_EXTRA_ECON = """- "topic_type": "제도/정책" | "금리/대출" | "생활 물가" | "경제 개념" | "제외" 중 하나. 개별 기업·종목 주가, 장중·일일 시황, 금융사·기업 인사, 기업 행사·수상·협약, 환율 트레이딩, 정치인 발언이 중심이면 "제외".
- "wallet_impact": 이 기사가 사회초년생(연봉 2,800~3,500만 원, 월급 230~290만 원)의 월급·대출·적금·세금·연금·보험·생활비에 주는 영향 한 문장. 원문으로 말할 수 없으면 null.
"""
FACTS_EXTRA_LISTING = """- "org": 주최 기관 이름 표기 목록(원문에 나온 그대로, 줄임말 포함 예: ["IBK기업은행", "기업은행"])
"""

def _v2_facts(title: str, article: str, listing: dict | None, model: str) -> dict:
    """1단계 사실표. 근거 문장을 원문과 대조해 틀린 항목은 버리고, 남은 게 3개 미만이면 한 번 더 뽑는다."""
    reader = config.CHANNEL_CONFIG.get("reader", config.CHANNEL_CONFIG["writer_intro"])
    extra = FACTS_EXTRA_ECON if CAPTION_RULES else FACTS_EXTRA_LISTING if listing else ""
    extra_example = ' "topic_type": "...", "wallet_impact": "...",' if CAPTION_RULES else ' "org": ["..."],' if listing else ""
    prompt = FACTS_PROMPT.format(reader=reader, extra_fields=extra, extra_example=extra_example)
    source = normalize_hanja(f"제목: {title}\n\n[원문]\n{article}")
    compact_article = _compact(article)
    last: Exception | None = None
    for _ in range(2):
        try:
            table = _parse(call_text_model(prompt, source, max_tokens=6000, model=model))
        except Exception as e:
            _raise_if_quota_exhausted(e)
            last = e
            continue
        kept, dropped = [], []
        for fact in table.get("facts") or []:
            evidence = _compact(str(fact.get("evidence") or ""))
            ok = len(evidence) >= 8 and (evidence in compact_article or fuzz.partial_ratio(evidence, compact_article) >= 92)
            nums_ok = all(_numbers(str(n)) <= _numbers(article) for n in fact.get("numbers") or [])
            (kept if ok and nums_ok and fact.get("id") else dropped).append(fact)
        table["facts"], table["dropped"] = kept, dropped
        if listing:
            table["org"] = [o for o in table.get("org") or [] if isinstance(o, str) and _compact(o) in compact_article]
        if len(kept) >= 3:
            return table
        last = ValueError(f"원문과 맞는 사실이 {len(kept)}개뿐(버린 항목 {len(dropped)}개)")
    raise SkipArticle(f"원문에서 사실표를 뽑지 못함: {last}")


WRITER_V2_PROMPT = """{intro}
[사실표]만 가지고 정해진 장 구조로 캐러셀 카드를 만든다.

[원칙]
1. 사실표에 있는 것만 쓴다. 모든 장에 사실표 항목을 1개 이상 담고 그 id를 "facts": ["F1"]에 적는다.
   독자 지갑에 바로 닿는 사실(공제·환급·지원 대상·조건·금액)을 통계·업계 숫자보다 먼저 담는다.
2. "○○에 따르면", "○○ 집계"의 ○○은 발표한 곳일 뿐 결정한 주체가 아니다(한국은행이 집계한 금리를 "한국은행이 올렸다"고 쓰지 않는다).
3. 빈말 금지: 숫자·고유명사·조건·행동이 하나도 없는 줄은 쓰지 않는다("꼼꼼히 확인해요", "관심 가져봐요", "알아두면 좋아요" 금지).
4. 전망은 우리가 예측하지 않는다. 기사·기관이 밝힌 전망만 "○○은 ~로 내다봤어요"처럼 누가 했는지 밝혀 쓴다.
5. 한자·일본어 금지. {tone}
6. 카드와 캡션에 사실표 id("F1")를 쓰지 않는다. id는 "facts"·"fact_placement" 칸에만.
{channel_rules}{calc_rule}
[장 구조] 각 장에 "role"을 적고 이 순서를 지킨다. 사실이 부족한 장은 빼도 되지만 {required}은 꼭 있어야 한다.
{structure}
[표지 제목 후보] "title_candidates"에 표지 제목 후보 10개를 쓴다. 코드가 아래 조건을 검사하고 통과한 후보 중 하나를 표지에 넣는다
(cover 장의 title은 비워 둔다).
- 공백 포함 18자 이내, "\\n"으로 나눈 2줄, 줄당 9자 안팎
- 핵심 명사 1개 이상({noun_rule})
- 구체적 숫자 1개 이상(금액·기간·인원·D-n·%). 숫자는 사실표 숫자이거나 calculations로 계산한 결과만. 지어낸 절약액·시간 금지
- 추상어 금지: {abstract}
- 예: {cover_example}

[장 글자]
- 표지 외 장: 머리글 "title"은 12자 이내. 본문(body·items·label·note·compare 칸을 합친 글자)은 공백 포함 60자 이내, 최대 3줄
  (body는 "\\n"으로 줄을 나누고, 목록은 항목 하나가 한 줄), 한 줄 20자 안팎. 줄은 짧은 명사구나 짧은 문장으로.
- 장마다 핵심 단어 1~2개를 **단어**처럼 별표 두 개로 감싼다(body·items·label·note 안에서만. title·value·kicker에는 쓰지 않는다).
- 60자에 못 넣는 사실은 버리지 말고 캡션 요약 줄이나 가이드로 보낸다.
  "fact_placement"에 사실표 모든 항목이 어디 들어갔는지 적는다: {{"F1": "2장", "F2": "캡션", "F3": "가이드"}}.
- 같은 종류 숫자 장(stat·compare)을 연달아 두지 않는다(합친다).

[장 배치] 각 장에 "layout": {{"tone": "밝음|어두움|강조색", "title_pos": "상단|중앙|하단", "margin": "넓게|보통", "icon": 아이콘}}.
- icon은 이 목록에서만: {icons}
- tone은 3장 연속 같지 않게. {compare_rule}

[장 종류] cover / stat(value 8자 이내 숫자 하나, label, note) / compare(items 2~3개 {{"label": 12자, "value": 10자}}, note) /
point(title, body) / list(title, items 2~4개) / closing(title, body){hook_rule}

[캡션] {caption_spec}{caption_rule}

[출력] JSON 하나만. 다른 말 금지.
{{"message": "핵심 메시지 한 문장", "title_candidates": ["...", "... 10개"],
 "slides": [{{"role": "표지", "kind": "cover", "title": "", "kicker": "...", "facts": ["F1"], "layout": {{...}}}}, ...],{calc_field}
 "fact_placement": {{"F1": "2장"}}, "caption": "...", "hashtags": ["{hashtag_example}", "..."],
 "image_prompt": "English. A concrete photographic scene (subject, setting, light, mood) that fits the topic. No text, no logos, no charts."{extra_out}}}"""

V2_CALC_RULE = """
[금액 계산] 이 계정의 핵심이다. "내 돈 계산" 장에 사회초년생 기준 원 단위 계산 결과를 넣는다(예: "월급 250만 원이면 한 달 3만 원 더").
- 계산 재료는 사실표 숫자만. 사실표에 없는 값은 가정으로만: 연봉 2,800~3,500만 원 또는 월급 230~290만 원 사이 값, 아니면 딱 떨어지는 예시 금액(대출 1,000만 원 등).
  가정은 그 장에 밝힌다("연봉 3,000만 원 기준"). 원문에 이미 있는 금액을 옮기는 건 계산이 아니다.
- 계산식은 "calculations"에 남긴다. 코드가 다시 계산해 틀리면 다시 쓰게 한다.
  형식: {"slide": 결과가 나오는 장 번호(1부터), "inputs": {"a": {"value": "171만원", "source": "원문"}, "p": {"value": "1,000만 원", "source": "가정", "label": "대출"}},
         "formula": "a - b"(재료 이름과 + - * / 괄호, 12·100 같은 환산 수만), "result": "5만 원"}
- 표지 제목에 금액을 쓰면 그 금액은 "내 돈 계산" 장에서 설명돼야 한다.
"""
V2_CAPTION_RULE_ECON = """
- 요약 3줄("• ")은 제목을 바꿔 말하지 않는다. 줄마다 사실표 항목 1개를 숫자나 조건과 함께 쓴다(세 줄이 서로 다른 사실)."""

# 장 구조(2026-11-02 운영자): 경제 6장·디징 4~5장. role 순서·필수 장을 코드가 검사한다.
STRUCTURES = {
    "money": {
        "roles": ["표지", "무슨 일", "흔한 착각", "내 돈 계산", "이렇게 하면", "전후 비교"],
        "required": ["표지", "내 돈 계산"],
        "text": """1 "표지"(cover)
2 "무슨 일": 기사 핵심 사실 1~2개(누가·무엇이·얼마·언제)
3 "흔한 착각": 독자가 실제로 하는 오해나 놓치는 것. 사실표에 근거가 있는 것만(없으면 이 장을 뺀다)
4 "내 돈 계산": 연봉·대출액 등 가정을 밝히고 원 단위 결과
5 "이렇게 하면": 대처 순서(1→2→3) 또는 확인할 것(list)
6 "전후 비교": kind "compare". 안 했을 때 vs 했을 때를 숫자로, note에 행동 한 줄(어디서 무엇을)""",
        "noun_rule": "기사 주제어",
        "cover_example": '"전세 이자\\n월 5만원 더 내요"',
    },
    "listing": {
        "roles": ["표지", "나 해당돼?", "신청 순서", "준비물·주의", "한눈 요약"],
        "required": ["표지", "나 해당돼?", "신청 순서"],
        "text": """1 "표지"(cover): 기관 + 무엇 + 마감
2 "나 해당돼?": 대상 조건 체크(list)
3 "신청 순서": 1→2→3, 어디서(list)
4 "준비물·주의": 준비 서류, 탈락 사유(복수 지원 금지 같은 것)
5 "한눈 요약"(선택): 마감·인원·링크 위치
"댓글 '가이드'면 DM" 안내는 카드에 쓰지 않는다(캡션에 코드가 붙인다).""",
        "noun_rule": "기관 이름 또는 제도·모집 이름",
        "cover_example": '"IBK 인턴 390명\\n11/16 마감"',
    },
}
COVER_MAX, COVER_LINE_MAX = 18, 11
CARD_TEXT_MAX, CARD_LINES_MAX, CARD_LINE_MAX, HEADING_MAX = 60, 3, 22, 12
ABSTRACT_WORDS = ("꿀팁", "알아야 할", "알아야할", "이것", "이거", "몰랐던", "모르면", "필수", "총정리", "비밀", "꼭 알아", "놓치면",
                  "손해", "무조건", "이렇게", "충격", "대박", "역대급", "알고 보니")
LAYOUT_TONES, LAYOUT_POS, LAYOUT_MARGIN = ("밝음", "어두움", "강조색"), ("상단", "중앙", "하단"), ("넓게", "보통")
# 렌더러(card_templates/_shared/engine.js ICON_PATHS)에 있는 아이콘만
LAYOUT_ICONS = ("돈", "달력", "체크", "경고", "화살표", "집", "카드", "서류", "사람", "비교 저울")
_EMPH_RE = re.compile(r"\*\*(.+?)\*\*")
_TEXT_KEYS = ("body", "label", "note", "subtitle")  # 표지 외 장에서 60자·강조를 보는 칸(+ items)


def _structure(listing: dict | None) -> dict | None:
    return STRUCTURES["listing"] if listing else STRUCTURES["money"] if REQUIRE_CALC else None


def _card_lines(slide: dict) -> list[str]:
    """표지 외 장의 본문 줄(강조 표시 포함): body·label·note의 줄 + 목록 항목 + 비교 칸."""
    lines: list[str] = []
    for key in ("body", "label", "note"):
        if isinstance(slide.get(key), str) and slide[key].strip():
            lines += [ln for ln in slide[key].split("\n") if ln.strip()]
    for item in slide.get("items") or []:
        lines.append(" ".join(str(v) for v in item.values()) if isinstance(item, dict) else str(item))
    return lines


def _plain(text: str) -> str:
    return _EMPH_RE.sub(r"\1", text)


def cover_problems(cand: str, terms: set[str], orgs: list[str], article: str, verified: list[float],
                   listing: dict | None) -> list[str]:
    """표지 후보 조건 검사. 문제 목록(비면 통과)."""
    problems = []
    lines = [ln for ln in cand.split("\n")]
    flat = "".join(lines)
    if len(flat) > COVER_MAX:
        problems.append(f"{len(flat)}자(18자 이내)")
    if len(lines) > 2 or any(len(ln) > COVER_LINE_MAX for ln in lines):
        problems.append("2줄·줄당 9자 안팎이 아님")
    compact = _compact(flat)
    names = list(terms) + [_compact(o) for o in orgs]
    if names and not any(n in compact for n in names):
        problems.append("핵심 명사 없음")
    if not re.search(r"\d", flat):
        problems.append("숫자 없음")
    if (word := next((w for w in ABSTRACT_WORDS if w in flat), None)):
        problems.append(f"추상어 {word!r}")
    rest = flat
    for m in _WON_RE.finditer(flat):  # 금액은 원문 값이거나 검증된 계산 결과만
        v = won_value(m.group(0))
        if not (any(_same(v, a) for a in article_values(article)) or any(abs(v - x) <= max(1.0, x * 0.01) for x in verified)):
            problems.append(f"원문·계산에 없는 금액 {m.group(0)!r}")
        rest = rest.replace(m.group(0), "")
    allowed = _numbers(article) | ({str(listing["days_left"])} if listing else set())
    made_up = [n for n in _numbers(rest) if len(n.replace(".", "")) >= 2 and n not in allowed]
    if made_up:
        problems.append(f"원문에 없는 숫자 {made_up}")
    return problems


def pick_cover(passing: list[str], title: str, core: str) -> dict:
    """통과한 표지 후보 중 하나를 고른다(편집 단계, 작은 호출). 기준: 탐색 탭에서 멈추게 하는가 + 제목만 봐도 무슨 일인지 아는가."""
    if len(passing) == 1:
        return {"title": passing[0], "reason": "통과한 후보가 하나뿐"}
    prompt = ("너는 인스타 카드뉴스 편집장이다. 아래 표지 제목 후보 중 하나를 고른다. 기준: 1) 인스타 탐색 탭에서 스크롤을 멈추게 하는가 "
              "2) 제목만 봐도 무슨 일이고 독자 돈·상황에 뭐가 바뀌는지 아는가. JSON만: {\"pick\": 번호, \"reason\": \"한 문장\"}\n\n"
              f"기사: {title}\n핵심: {core}\n후보: " + json.dumps({i + 1: c for i, c in enumerate(passing)}, ensure_ascii=False))
    try:
        raw = call_text_model("반드시 유효한 JSON 한 객체만 반환한다.", prompt, max_tokens=300)
        got = _parse(raw)
        n = int(got.get("pick"))
        if 1 <= n <= len(passing):
            return {"title": passing[n - 1], "reason": str(got.get("reason") or "")[:120]}
    except Exception as e:
        _raise_if_quota_exhausted(e)
        print(f"[slide_writer v2] 표지 고르기 실패, 첫 통과 후보를 씀: {e}", file=sys.stderr)
    return {"title": passing[0], "reason": "고르기 실패로 첫 통과 후보"}


def check_v2(deck: dict, table: dict, listing: dict | None, title: str, verified: list[float] | None = None) -> None:
    """v2 구조 검사: 장 역할·순서, 장마다 사실표 항목, 60자·3줄·머리글 12자, 강조 1~2개, layout, 숫자 장 연속,
    사실 배치 기록, 빈말, 표지 금액은 계산 장에서 설명, 마지막 행동, 캡션 요약 줄. 문제를 모아 한 번에 올린다."""
    problems: list[str] = []
    facts = {f["id"]: f for f in table["facts"]}
    slides = deck.get("slides") or []
    structure = _structure(listing)
    roles = [s.get("role") for s in slides]
    if structure:
        order = structure["roles"]
        unknown = [r for r in roles if r not in order]
        if unknown:
            problems.append(f"모르는 role {unknown}(쓸 수 있는 것: {order})")
        else:
            idx = [order.index(r) for r in roles]
            if idx != sorted(idx) or len(set(idx)) != len(idx):
                problems.append(f"장 순서가 구조와 다름: {roles}(순서: {order})")
        missing = [r for r in structure["required"] if r not in roles]
        if missing:
            problems.append(f"필수 장 없음: {missing}")
        if "전후 비교" in roles and slides[roles.index("전후 비교")].get("kind") != "compare":
            problems.append("\"전후 비교\" 장은 kind \"compare\"여야 함")
    tones = []
    for i, slide in enumerate(slides, 1):
        refs = slide.get("facts")
        if not isinstance(refs, list) or not refs or any(r not in facts for r in refs):
            problems.append(f"{i}장 \"facts\"에 사실표 id가 없거나 틀림({refs!r})")
            refs = [r for r in refs or [] if r in facts] if isinstance(refs, list) else []
        lay = slide.get("layout")
        if not isinstance(lay, dict):
            problems.append(f"{i}장 layout 칸 없음")
        else:
            bad = [k for k, ok in (("tone", LAYOUT_TONES), ("title_pos", LAYOUT_POS), ("margin", LAYOUT_MARGIN), ("icon", LAYOUT_ICONS))
                   if lay.get(k) not in ok]
            if bad:
                problems.append(f"{i}장 layout {bad} 값이 목록 밖: {lay}")
            tones.append(lay.get("tone"))
        for key in ("title", "value", "kicker"):
            if "**" in str(slide.get(key) or ""):
                problems.append(f"{i}장 {key}에 ** 강조(본문·목록·label·note에만)")
        text = " ".join(_texts(slide))
        if (m := _VAGUE_RE.search(text)):
            problems.append(f"{i}장 정보 없는 문장({m.group(0)!r})")
        if i == 1:
            continue
        heading = str(slide.get("title") or "").replace("\n", "")
        if len(heading) > HEADING_MAX:
            problems.append(f"{i}장 머리글 {len(heading)}자(12자 이내): {heading!r}")
        lines = _card_lines(slide)
        body = "".join(_plain(ln) for ln in lines)
        if len(body) > CARD_TEXT_MAX:
            problems.append(f"{i}장 본문 {len(body)}자(60자 이내, 넘치는 사실은 캡션·가이드로): {body!r}")
        if len(lines) > CARD_LINES_MAX:
            problems.append(f"{i}장 본문 {len(lines)}줄(3줄 이내)")
        long = [ln for ln in lines if len(_plain(ln)) > CARD_LINE_MAX]
        if long:
            problems.append(f"{i}장 한 줄이 20자를 크게 넘음: {_plain(long[0])!r}")
        marks = sum(len(_EMPH_RE.findall(ln)) for ln in lines)
        if not 1 <= marks <= 2:
            problems.append(f"{i}장 **강조** {marks}개(장마다 핵심 단어 1~2개)")
        if any(ln.count("**") % 2 for ln in lines):
            problems.append(f"{i}장 ** 짝이 안 맞음")
        fact_words = set().union(*(_stems(facts[r]["fact"]) for r in refs)) if refs else set()
        if not (re.search(r"\d", text) or _CONDITION_RE.search(text) or _ACTION_RE.search(text) or len(_stems(text) & fact_words) >= 2):
            problems.append(f"{i}장에 사실표 내용(숫자·대상·조건·고유명사)이 안 보임")
        if listing and "DM" in text:
            problems.append(f"{i}장 \"댓글 '가이드'면 DM\" 안내는 캡션에만")
    for i in range(2, len(tones)):
        if tones[i] and tones[i] == tones[i - 1] == tones[i - 2]:
            problems.append(f"{i - 1}~{i + 1}장 tone이 3장 연속 {tones[i]!r}")
    kinds = [s.get("kind") for s in slides]
    for i in range(1, len(kinds)):
        if kinds[i] in ("stat", "compare") and kinds[i - 1] in ("stat", "compare"):
            problems.append(f"{i}~{i + 1}장 숫자 장({kinds[i - 1]}·{kinds[i]})이 연달아 있음(합칠 것)")
    placement = deck.get("fact_placement")
    if not isinstance(placement, dict):
        problems.append("fact_placement 칸 없음(사실표 모든 항목이 카드·캡션·가이드 중 어디 갔는지)")
    else:
        missing = [f for f in facts if f not in placement]
        if missing:
            problems.append(f"fact_placement에 빠진 사실 {missing}")
        for fid, where in placement.items():
            m = re.fullmatch(r"(\d+)장", str(where).strip())
            if m and not (1 <= int(m.group(1)) <= len(slides) and fid in (slides[int(m.group(1)) - 1].get("facts") or [])):
                problems.append(f"fact_placement {fid}: {where}인데 그 장 facts에 없음")
            elif not m and str(where).strip() not in ("캡션", "가이드"):
                problems.append(f"fact_placement {fid}: {where!r}(\"N장\"·\"캡션\"·\"가이드\" 중 하나)")
    cover_title = str(slides[0].get("title") or "") if slides else ""
    if REQUIRE_CALC and structure and "내 돈 계산" in roles:  # 표지에서 말한 금액은 계산 장에서 설명돼야
        calc_text = " ".join(_texts(slides[roles.index("내 돈 계산")]))
        for m in _WON_RE.finditer(cover_title):
            v = won_value(m.group(0))
            if not any(abs(won_value(x.group(0)) - v) <= max(1.0, v * 0.01) for x in _WON_RE.finditer(calc_text)):
                problems.append(f"표지 금액 {m.group(0)!r}이 \"내 돈 계산\" 장에 없음")
    last = " ".join(_texts(slides[-1])) if slides else ""
    action = table.get("action") or {}
    where = _stems(str(action.get("where") or "")) if isinstance(action, dict) else set()
    if len(slides) > 1:
        if where and not (any(w in _compact(last) for w in where) and _ACTION_RE.search(last)):
            problems.append(f"마지막 장에 행동 한 줄이 없음(어디서 무엇을: {action.get('where')} / {action.get('what')})")
        if not where and not (re.search(r"\d", last) or _ACTION_RE.search(last)):
            problems.append("마지막 장에 행동도 숫자도 없음")
    if CAPTION_RULES:
        plain_title = normalize_title(title)
        for line in str(deck.get("caption") or "").splitlines():
            line = line.strip()
            if line.startswith("•"):
                if not (re.search(r"\d", line) or _CONDITION_RE.search(line)):
                    problems.append(f"캡션 요약 줄에 숫자·조건이 없음: {line!r}")
                if fuzz.ratio(normalize_title(line), plain_title) >= 60:
                    problems.append(f"캡션 요약 줄이 기사 제목을 바꿔 말한 것: {line!r}")
    if problems:
        raise ValueError("\n".join(dict.fromkeys(problems)))


_FACT_ID_RE = re.compile(r"\s*[(\[]\s*F\d+(?:\s*[,·]\s*F\d+)*\s*[)\]]")


def _strip_fact_ids(deck: dict) -> dict:
    """모델이 문구 끝에 붙인 "(F1)" 같은 사실표 id를 지운다."""
    for slide in deck.get("slides") or []:
        for key, value in list(slide.items()):
            if isinstance(value, str) and key != "kind":
                slide[key] = _FACT_ID_RE.sub("", value)
        if isinstance(slide.get("items"), list):
            slide["items"] = [{k: _FACT_ID_RE.sub("", v) if isinstance(v, str) else v for k, v in x.items()} if isinstance(x, dict)
                              else _FACT_ID_RE.sub("", x) if isinstance(x, str) else x for x in slide["items"]]
    if isinstance(deck.get("caption"), str):
        deck["caption"] = _FACT_ID_RE.sub("", deck["caption"])
    return deck


def v2_system_prompt(listing: dict | None) -> str:
    rules = [r for r in config.CHANNEL_CONFIG["writer_rules"] if "topic_type" not in r and "wallet_impact" not in r]
    structure = _structure(listing) or {"roles": [], "required": ["표지"], "text": "자유 구성(표지 먼저)", "noun_rule": "기사 주제어",
                                         "cover_example": '"전세 이자\\n월 5만원 더 내요"'}
    tone = config.CHANNEL_CONFIG["tone_rule"] if BANMAL else (
        '말투는 친근한 해요체("~예요/~해요"). 줄은 짧은 명사구로 끝나도 된다. "~습니다/~합니다"·기사체("~했다")는 쓰지 않는다.')
    return WRITER_V2_PROMPT.format(
        intro=config.CHANNEL_CONFIG["writer_intro"], tone=tone,
        channel_rules="".join(f"{i}. {r}\n" for i, r in enumerate(rules, 7)),
        calc_rule=V2_CALC_RULE if REQUIRE_CALC else "",
        required=", ".join(f'"{r}"' for r in structure["required"]), structure=structure["text"],
        noun_rule=structure["noun_rule"], abstract=", ".join(f'"{w}"' for w in ABSTRACT_WORDS[:12]),
        cover_example=structure["cover_example"], icons=", ".join(LAYOUT_ICONS),
        compare_rule='"전후 비교" 장은 kind "compare"(비교 레이아웃).' if REQUIRE_CALC else "",
        hook_rule=". cover에 hook(채널 규칙에 있을 때만, 각 22자 이내)" if any("hook" in r for r in rules) else "",
        caption_spec=CAPTION_SPEC, caption_rule=V2_CAPTION_RULE_ECON if CAPTION_RULES else "",
        calc_field=' "calculations": [],' if REQUIRE_CALC else "",
        hashtag_example=config.CHANNEL_CONFIG["hashtag_example"],
        extra_out=', "ended": false' if CHECK_DEADLINE else "",
    )


def tone_errors(deck: dict) -> list[str]:
    """기사체·합쇼체 줄을 한 번에 모두 찾는다(v2는 짧은 명사구 줄을 허용해 해요체 끝맺음은 강제하지 않는다)."""
    found = []
    for i, slide in enumerate(deck.get("slides") or [], 1):
        for line in _card_lines(slide) + [str(slide.get("title") or "")]:
            for sentence in _SENTENCE_SPLIT_RE.split(_plain(line).strip()):
                if sentence and _ARTICLE_END_RE.search(sentence):
                    found.append(f"{i}장 말투(합쇼체·기사체): {sentence!r}")
    return found


def _v2_checks(deck: dict, table: dict, article: str, title: str, published, listing, outlet) -> list[float]:
    """코드 검사 전부. 문제를 모아 ValueError 하나로. 통과하면 검증된 계산 금액을 돌려준다."""
    problems = tone_errors(deck)
    verified: list[float] = []
    try:
        verified = check_calculations(deck, article)
    except NoCalculation:
        raise
    except ValueError as e:
        problems.append(str(e))
    for check in (lambda: check_deck(deck, article, published, listing, title=title, outlet=outlet, v2=True),
                  lambda: check_v2(deck, table, listing, title, verified)):
        try:
            check()
        except (SkipArticle, NoCalculation):
            raise
        except ValueError as e:
            problems.append(str(e))
    if problems:
        raise ValueError("\n".join(dict.fromkeys(problems)))
    return verified


def _choose_cover(deck: dict, table: dict, article: str, title: str, listing: dict | None) -> None:
    """후보 10개를 검사해 deck["cover_candidates"]에 남기고, 통과한 것 중 첫 번째를 일단 표지에 넣는다(최종 선택은 pick_cover)."""
    cands = [str(c).replace("\\n", "\n").strip() for c in deck.get("title_candidates") or [] if isinstance(c, str) and c.strip()]
    if len(cands) < 10:
        raise ValueError(f"title_candidates가 {len(cands)}개(10개)")
    terms = set()
    for t in (table.get("title_terms") or []) + [title]:
        terms |= _stems(str(t))
    try:
        verified = check_calculations(deck, article)
    except ValueError:
        verified = []
    orgs = [o for o in (table.get("org") or []) + (host_names(listing.get("host")) if listing else []) if o]
    rows = [{"title": c, "problems": cover_problems(c, terms, orgs, article, verified, listing)} for c in cands]
    deck["cover_candidates"] = rows
    passing = [r["title"] for r in rows if not r["problems"]]
    if not passing:
        raise ValueError("표지 후보 10개가 모두 조건 탈락: " + " / ".join(f"{r['title']!r} {r['problems']}" for r in rows[:5]))
    deck["slides"][0]["title"] = passing[0]


DESIGNER_PROMPT = """너는 카드뉴스 외주 10년차 디자이너야. 이 덱(장별 문구 + layout + 렌더 QA 결과: 넘침·겹침·줄 수)을 보고
납품 전에 반드시 고칠 곳 3개와 고친 문장을 써라. 사실·숫자는 바꾸지 말고(원문 숫자·계산 결과 그대로), 장 구조와 글자 규칙
(표지 외 장 본문 60자·3줄, 머리글 12자, 장마다 **강조** 1~2개)을 지킨다. 고칠 칸은 title·body·label·note·kicker·subtitle 중 하나,
목록은 "items.0"처럼 번호까지.
JSON만: {"fixes": [{"slide": 장 번호, "field": "body", "before": "지금 문구", "after": "고친 문구", "reason": "왜(한 문장)"}, ...3개]}"""


def designer_review(deck: dict, render_qa: str, article: str, title: str, published, listing, outlet,
                    model: str | None = None) -> tuple[dict, list[dict], float]:
    """렌더 뒤 검수 1회. 고친 문장을 하나씩 반영해 검사를 통과한 것만 남긴다. (새 덱, 검수 3개, 뉴런)."""
    import copy
    import llm_client

    title, article = strip_outlet(title), clean_article(article)
    before = llm_client.NEURONS_USED
    view = [{"slide": i, "role": s.get("role"), "kind": s.get("kind"), "layout": s.get("layout"),
             **{k: s.get(k) for k in ("title", "kicker", "subtitle", "body", "label", "note", "value", "items") if s.get(k)},
             "lines": len(_card_lines(s)), "chars": len("".join(_plain(x) for x in _card_lines(s)))}
            for i, s in enumerate(deck.get("slides") or [], 1)]
    request = f"기사: {title}\n\n[덱]\n{json.dumps(view, ensure_ascii=False)}\n\n[렌더 QA]\n{render_qa or '경고 없음'}"
    try:
        fixes = _parse(call_text_model(DESIGNER_PROMPT, request, max_tokens=3000, model=model or MODEL)).get("fixes") or []
    except Exception as e:
        _raise_if_quota_exhausted(e)
        return deck, [{"error": f"검수 실패: {e}"}], llm_client.NEURONS_USED - before
    table = deck.get("fact_table") or {"facts": []}
    current = deck
    results = []
    for fix in fixes[:3]:
        row = {k: fix.get(k) for k in ("slide", "field", "before", "after", "reason")}
        try:
            trial = copy.deepcopy(current)
            slide = trial["slides"][int(fix["slide"]) - 1]
            field = str(fix["field"])
            if field.startswith("items."):
                slide["items"][int(field.split(".")[1])] = fix["after"]
            elif field in ("title", "body", "label", "note", "kicker", "subtitle"):
                slide[field] = fix["after"]
            else:
                raise ValueError(f"고칠 수 없는 칸 {field!r}")
            caption_body = trial["caption"]  # 덱 캡션은 고지·해시태그가 붙은 최종본이라 캡션 규칙 검사는 건너뛴다
            trial["caption"] = deck.get("caption_body") or caption_body
            _v2_checks(trial, table, article + " " + title, title, published, listing, outlet)
            trial["caption"] = caption_body
            current, row["applied"] = trial, True
        except Exception as e:
            row.update(applied=False, rejected=str(e)[:200])
        results.append(row)
    neurons = llm_client.NEURONS_USED - before
    print(f"[slide_writer v2] 디자이너 검수 {neurons:.0f} 뉴런, 반영 {sum(1 for r in results if r.get('applied'))}/{len(results)}", file=sys.stderr)
    return current, results, neurons


def write_deck_v2(title: str, article: str, category: str, hint: str = "", published: date | None = None,
                  listing: dict | None = None, outlet: str | None = None, model: str | None = None) -> dict:
    """1단계 사실표 → 2단계 카드(표지 후보 10개 포함) → 코드 검사(문제를 모아서) → 표지 고르기.
    걸리면 걸린 이유를 붙여 고쳐 쓰게 한다(최대 2번). 금액 계산 채널에서 계산 장이 두 번 없으면 SkipArticle."""
    model = model or MODEL
    title = strip_outlet(title)
    article = clean_article(article)
    if CHECK_DEADLINE and not listing:
        check_post_event(article, published)
    table = _v2_facts(title, article, listing, FACTS_MODEL if model == MODEL else model)
    if CAPTION_RULES:  # 경제: 타깃 밖 주제·지갑 영향 없는 기사는 카드를 쓰기 전에 끊는다
        if str(table.get("topic_type") or "").strip() == "제외":
            raise SkipArticle("주제 필터: 종목·시황·인사·행사·환율 트레이딩·정치 발언 기사(topic_type=제외)라 게시하지 않음")
        if "wallet_impact" in table and not str(table.get("wallet_impact") or "").strip():
            raise SkipArticle("독자 지갑 영향(월급·대출·적금·세금 등)을 말할 수 없는 기사라 게시하지 않음")
    system = v2_system_prompt(listing)
    shown = {k: table.get(k) for k in ("core", "title_terms", "action", "org", "wallet_impact") if table.get(k)}
    shown["facts"] = [{k: f.get(k) for k in ("id", "fact", "numbers", "who", "when")} for f in table["facts"]]
    source = normalize_hanja(f"카테고리: {config.CATEGORY_LABELS.get(category, category)}\n기사 제목: {title}\n\n"
                             f"[사실표]\n{json.dumps(shown, ensure_ascii=False, indent=0)}")
    if listing:
        source += (f"\n\n[공고 카드 규칙] 원문은 지금 신청할 수 있는 공식 모집 공고다. 덱의 \"ended\"는 false. 오늘 기준 D-{listing['days_left']}. "
                   f"캡션 {CAPTION_DATE_MARK} 줄에 신청 기간과 \"D-{listing['days_left']}\"를 그대로 쓴다. "
                   f"캡션 ✅ 신청 방법 줄에 공식 기관명 \"{display_host(listing.get('host'))}\"을 쓴다.")
    if hint:
        source += f"\n\n[지난번 카드 렌더에서 걸린 문제 — 이번엔 피할 것]\n{hint}"
    check_text = article + " " + title
    deck, feedback, no_calc, last_error = None, "", 0, None
    for attempt in range(1, 4):  # 작성 1번 + 고쳐 쓰기 최대 2번
        request = source
        if deck is not None:
            request += f"\n\n[지난 덱]\n{json.dumps(deck, ensure_ascii=False)}\n\n[검사에서 걸린 문제 — 반드시 고칠 것]\n{feedback}"
        try:
            deck = _strip_fact_ids(_parse(call_text_model(system, request, max_tokens=10000, model=model)))
            if CHECK_DEADLINE and not listing and deck.get("ended") is True and not has_upcoming_date(article, published):
                raise SkipArticle("이미 끝난 행사·모집이라 게시하지 않음(작성 단계 판단)")
            if not deck.get("slides"):
                raise ValueError("slides가 비어 있음")
            _choose_cover(deck, table, article, title, listing)
            _v2_checks(deck, table, check_text, title, published, listing, outlet)
            tags = " ".join("#" + t for t in clean_hashtags(deck.get("hashtags")))
            disclaimer = listing_disclaimer(listing.get("host")) if listing else DISCLAIMER
            body = str(deck.get("caption") or "").strip()
            final_caption = comment_dm.add_line(f"{body}\n\n{chr(10).join(p for p in (disclaimer, tags) if p)}".strip())
            if len(final_caption) > CAPTION_HARD_MAX:
                raise ValueError(f"캡션이 고지·해시태그 포함 {len(final_caption)}자(인스타 한도 {CAPTION_HARD_MAX}자) — 본문을 줄일 것")
        except SkipArticle:
            raise
        except NoCalculation as e:
            no_calc += 1
            if no_calc >= 2:
                raise SkipArticle(f"금액 계산 불가: {e}") from e
            last_error, feedback = e, str(e)
            print(f"[slide_writer v2] {attempt}번째 덱: {e}", file=sys.stderr)
            continue
        except Exception as e:
            _raise_if_quota_exhausted(e)
            last_error, feedback = e, str(e)
            print(f"[slide_writer v2] {attempt}번째 덱 검사 실패: {e}", file=sys.stderr)
            if not isinstance(deck, dict):
                deck = None
            continue
        passing = [r["title"] for r in deck["cover_candidates"] if not r["problems"]]
        pick = pick_cover(passing, title, str(table.get("core") or ""))
        deck["slides"][0]["title"] = pick["title"]
        deck["cover_pick"] = pick
        deck["caption_body"] = body
        deck["caption"] = final_caption
        deck["fact_table"] = table  # 검토용(어느 사실이 어느 장에 갔는지)
        facts = {f["id"]: f for f in table["facts"]}
        deck["guide_notes"] = [facts[f]["fact"] for f, where in (deck.get("fact_placement") or {}).items()
                               if str(where).strip() == "가이드" and f in facts]
        deck["image_prompt"] = deck.get("image_prompt") if isinstance(deck.get("image_prompt"), str) and not str(
            deck.get("image_prompt")).startswith(_IMAGE_PROMPT_EXAMPLE) else None
        deck["props"] = clean_props(deck.get("props"), article + " " + title, listing.get("host") if listing else None)
        return deck
    raise RuntimeError(f"슬라이드 생성(v2) 3회 실패: {last_error}")


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    item = json.load(open(src, encoding="utf-8"))
    deck = write_deck(item["title"], item["article"], item["category"])
    deck["source"] = {"title": item["title"], "outlet": item.get("source"), "url": item.get("url")}
    json.dump(deck, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(deck, ensure_ascii=False, indent=1))

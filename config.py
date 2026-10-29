import logging
import os
import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")

CLOUDFLARE_TEXT_MODEL = os.environ.get("CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
POSE_GEN_MODEL = os.environ.get("POSE_GEN_MODEL", "@cf/black-forest-labs/flux-1-schnell")

INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
INSTAGRAM_API_VERSION = os.environ.get("INSTAGRAM_API_VERSION", "v21.0")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "cards")
INSTAGRAM_HANDLE = os.environ.get("INSTAGRAM_HANDLE", "@myohan.economy")

MAX_BACKLOG_DAYS = int(os.environ.get("MAX_BACKLOG_DAYS", 3))
SAFE_DAILY_CLOUDFLARE_CALLS = int(os.environ.get("SAFE_DAILY_CLOUDFLARE_CALLS", 12))

MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", 24))
MIN_LENGTH = int(os.environ.get("MIN_LENGTH", 50))
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", 70))
TITLE_MAX_CHARS = int(os.environ.get("TITLE_MAX_CHARS", 15))
CANDIDATES_PER_CATEGORY = int(os.environ.get("CANDIDATES_PER_CATEGORY", 5))
SELECTION_POOL_PER_CATEGORY = int(os.environ.get("SELECTION_POOL_PER_CATEGORY", 20))
RECENT_TOPIC_SIMILARITY_THRESHOLD = float(os.environ.get("RECENT_TOPIC_SIMILARITY_THRESHOLD", 78))
RECENT_TOPIC_LOOKBACK = int(os.environ.get("RECENT_TOPIC_LOOKBACK", 30))
QA_MAX_ATTEMPTS = int(os.environ.get("QA_MAX_ATTEMPTS", 3))
QA_MIN_ARTICLE_CHARS = int(os.environ.get("QA_MIN_ARTICLE_CHARS", 400))

CATEGORIES = ("index_macro", "stock", "policy_industry")
CATEGORY_LABELS = {
    "index_macro": "지수/거시",
    "stock": "개별종목",
    "policy_industry": "정책/산업",
}

# 레포가 public이고 GitHub Actions 로그도 공개되므로, 어떤 경로로 찍히든
# 비밀값이 로그·텔레그램·pipeline_events에 남지 않도록 여기서 일괄 가린다.
# (2026-10-29 확인: httpx INFO 로그가 access_token 쿼리 파라미터를 URL째 남기고 있었음)
_SECRETS = [
    s for s in (
        SUPABASE_SERVICE_ROLE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET,
        CLOUDFLARE_API_TOKEN, INSTAGRAM_ACCESS_TOKEN,
    ) if len(s) >= 8
]


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    return text


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# 각 스크립트가 config를 먼저 import한 뒤 basicConfig를 호출하므로, 여기서 먼저
# 핸들러를 달아두면 이후 basicConfig 호출은 아무 일도 하지 않고 이 포매터가 쓰인다.
_handler = logging.StreamHandler()
_handler.setFormatter(_RedactingFormatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logging.getLogger("httpx").setLevel(logging.WARNING)


def _redacting_excepthook(exc_type, exc, tb):
    sys.stderr.write(redact("".join(traceback.format_exception(exc_type, exc, tb))))


sys.excepthook = _redacting_excepthook

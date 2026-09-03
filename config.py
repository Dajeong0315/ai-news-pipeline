import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")

CLOUDFLARE_TEXT_MODEL = os.environ.get("CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.1-8b-instruct")
POSE_GEN_MODEL = os.environ.get("POSE_GEN_MODEL", "@cf/black-forest-labs/flux-1-schnell")

INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
INSTAGRAM_API_VERSION = os.environ.get("INSTAGRAM_API_VERSION", "v21.0")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "cards")

MAX_BACKLOG_DAYS = int(os.environ.get("MAX_BACKLOG_DAYS", 3))
SAFE_DAILY_CLOUDFLARE_CALLS = int(os.environ.get("SAFE_DAILY_CLOUDFLARE_CALLS", 12))

MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", 24))
MIN_LENGTH = int(os.environ.get("MIN_LENGTH", 50))
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", 70))
TITLE_MAX_CHARS = int(os.environ.get("TITLE_MAX_CHARS", 15))
CANDIDATES_PER_CATEGORY = int(os.environ.get("CANDIDATES_PER_CATEGORY", 5))

CATEGORIES = ("index_macro", "stock", "policy_industry")
CATEGORY_LABELS = {
    "index_macro": "지수/거시",
    "stock": "개별종목",
    "policy_industry": "정책/산업",
}

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

GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash")

MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", 24))
MIN_LENGTH = int(os.environ.get("MIN_LENGTH", 50))
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", 85))
TITLE_MAX_CHARS = int(os.environ.get("TITLE_MAX_CHARS", 30))

CATEGORIES = ("index_macro", "stock", "policy_industry")

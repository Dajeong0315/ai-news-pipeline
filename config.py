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

MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", 24))
MIN_LENGTH = int(os.environ.get("MIN_LENGTH", 50))
DEDUP_SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY_THRESHOLD", 85))
TITLE_MAX_CHARS = int(os.environ.get("TITLE_MAX_CHARS", 15))
CANDIDATES_PER_CATEGORY = int(os.environ.get("CANDIDATES_PER_CATEGORY", 5))
VISION_MODEL = os.environ.get("VISION_MODEL", "@cf/llava-hf/llava-1.5-7b-hf")
MAX_IMAGE_RETRIES = int(os.environ.get("MAX_IMAGE_RETRIES", 1))
FONT_PATH = os.environ.get("FONT_PATH", "C:/Windows/Fonts/malgun.ttf")
FONT_BOLD_PATH = os.environ.get("FONT_BOLD_PATH", "C:/Windows/Fonts/malgunbd.ttf")

CATEGORIES = ("index_macro", "stock", "policy_industry")
CATEGORY_LABELS = {
    "index_macro": "지수/거시",
    "stock": "개별종목",
    "policy_industry": "정책/산업",
}

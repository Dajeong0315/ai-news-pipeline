from functools import lru_cache

from supabase import Client, create_client

import config


@lru_cache(maxsize=1)
def get_client() -> Client:
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY가 .env에 설정되어 있지 않습니다."
        )
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

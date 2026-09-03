"""Instagram Graph API로 카드 이미지를 업로드/게시한다.

Instagram Graph API는 로컬 파일을 직접 받지 않고 공개 HTTPS URL만 받으므로,
Supabase Storage(public 버킷 `cards`)에 먼저 올려 공개 URL을 만든 뒤 그 URL로
media 컨테이너를 생성 -> 게시하는 2단계로 진행한다.

필요 환경변수: INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_BUSINESS_ACCOUNT_ID
(둘 다 Meta for Developers 앱 + Graph API Explorer에서 발급받아야 하며,
이 저장소의 get_instagram_account_id.py가 계정 ID를 찾는 걸 도와준다)
"""

import time
from pathlib import Path

import httpx

import config
from db import get_client

GRAPH_BASE = f"https://graph.facebook.com/{config.INSTAGRAM_API_VERSION}"


def upload_to_storage(local_path: str) -> str:
    client = get_client()
    bucket = client.storage.from_(config.SUPABASE_STORAGE_BUCKET)
    # 카드 파일명이 01.png처럼 카테고리/날짜 사이에 겹칠 수 있어, 폴더 구조를
    # 그대로 스토리지 경로에 반영해 충돌을 피한다(예: 2026-09-03/stock/01.png).
    path = Path(local_path)
    storage_path = "/".join(path.parts[-3:]) if len(path.parts) >= 3 else path.name
    data = path.read_bytes()
    bucket.upload(
        path=storage_path,
        file=data,
        file_options={"content-type": "image/png", "upsert": "true"},
    )
    return bucket.get_public_url(storage_path)


def create_carousel_item(image_url: str) -> str:
    resp = httpx.post(
        f"{GRAPH_BASE}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/media",
        data={
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": config.INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def create_carousel_container(child_ids: list[str], caption: str) -> str:
    resp = httpx.post(
        f"{GRAPH_BASE}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": config.INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def create_media_container(image_url: str, caption: str) -> str:
    """단일 이미지 게시용(호환용, 캐러셀이 아닌 경우)."""
    resp = httpx.post(
        f"{GRAPH_BASE}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/media",
        data={
            "image_url": image_url,
            "caption": caption,
            "access_token": config.INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def wait_until_ready(creation_id: str, max_wait_sec: int = 30, poll_interval_sec: int = 3) -> None:
    """미디어 컨테이너 처리가 끝날 때까지 대기(FINISHED 상태 폴링)."""
    waited = 0
    while waited < max_wait_sec:
        resp = httpx.get(
            f"{GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": config.INSTAGRAM_ACCESS_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"인스타그램 미디어 처리 실패: {resp.json()}")
        time.sleep(poll_interval_sec)
        waited += poll_interval_sec


def publish_media(creation_id: str) -> str:
    resp = httpx.post(
        f"{GRAPH_BASE}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": config.INSTAGRAM_ACCESS_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def upload_and_publish_carousel(local_paths: list[str], caption: str) -> str:
    """여러 장의 카드 이미지를 인스타그램 캐러셀(여러 장 게시물) 하나로 올린다."""
    child_ids = []
    for path in local_paths:
        image_url = upload_to_storage(path)
        child_ids.append(create_carousel_item(image_url))

    container_id = create_carousel_container(child_ids, caption)
    wait_until_ready(container_id)
    return publish_media(container_id)


def upload_and_publish(local_path: str, caption: str) -> str:
    image_url = upload_to_storage(local_path)
    creation_id = create_media_container(image_url, caption)
    wait_until_ready(creation_id)
    return publish_media(creation_id)

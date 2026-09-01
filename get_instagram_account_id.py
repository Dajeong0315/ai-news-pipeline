"""Graph API Explorer에서 발급받은 User Access Token으로, 연결된 Facebook 페이지와
그 페이지에 연결된 인스타그램 비즈니스 계정 ID를 찾아준다.

토큰 발급 방법:
    1. https://developers.facebook.com/tools/explorer/ 접속
    2. 우측 상단에서 본인이 만든 Meta 앱 선택
    3. Permissions에 pages_show_list, pages_read_engagement, instagram_basic,
       instagram_content_publish 추가 후 "Generate Access Token" 클릭
    4. 로그인/권한 승인 후 나온 토큰을 아래 명령의 인자로 사용

실행:
    python get_instagram_account_id.py <user_access_token>

출력되는 "페이지 액세스 토큰"이 .env의 INSTAGRAM_ACCESS_TOKEN 후보다(장기 토큰
교환은 별도로 진행 권장, PROGRESS.md 참고). "인스타그램 비즈니스 계정 ID"가
.env의 INSTAGRAM_BUSINESS_ACCOUNT_ID다.
"""

import sys

import httpx

import config

GRAPH_BASE = f"https://graph.facebook.com/{config.INSTAGRAM_API_VERSION}"


def main():
    if len(sys.argv) != 2:
        print("사용법: python get_instagram_account_id.py <user_access_token>")
        sys.exit(1)
    token = sys.argv[1]

    resp = httpx.get(f"{GRAPH_BASE}/me/accounts", params={"access_token": token}, timeout=30)
    resp.raise_for_status()
    pages = resp.json().get("data", [])

    if not pages:
        print("연결된 Facebook 페이지가 없습니다. 인스타그램 계정과 페이지 연결을 먼저 확인하세요.")
        return

    for page in pages:
        ig_resp = httpx.get(
            f"{GRAPH_BASE}/{page['id']}",
            params={"fields": "instagram_business_account", "access_token": page["access_token"]},
            timeout=30,
        )
        ig_account = ig_resp.json().get("instagram_business_account", {}).get("id")
        print(f"페이지: {page['name']} (id={page['id']})")
        print(f"  페이지 액세스 토큰: {page['access_token']}")
        print(f"  연결된 인스타그램 비즈니스 계정 ID: {ig_account or '(연결 안 됨)'}")
        print()


if __name__ == "__main__":
    main()

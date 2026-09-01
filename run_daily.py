"""파이프라인 전체를 순서대로 1회 실행한다 (수동/스케줄 공용).

collect -> clean -> send_candidates 까지 실행한 뒤, 운영자가 텔레그램에서
카테고리당 1건씩(이월분이 있으면 그 이상) 승인할 때까지 기다렸다가, 승인이
끝나면 이어서 prompt -> image -> verify -> compose -> instagram 단계를
실행하면 된다. (승인은 사람이 텔레그램에서 하는 유일한 개입 지점이라 자동
대기시키지 않는다.) publish 단계 마지막에는 오늘 API 호출량 이상 여부도
확인한다.

실행:
    python run_daily.py collect   # 수집+정제+후보 전송까지
    python run_daily.py publish   # 승인 완료 후 프롬프트~카드 합성~인스타 업로드까지
"""

import sys

import check_usage
import clean_news
import collect_news
import compose_card
import generate_image
import generate_prompt
import publish_instagram
import send_candidates
import verify_image


def run_collect_phase():
    collect_news.main()
    clean_news.main()
    send_candidates.main()


def run_publish_phase():
    generate_prompt.main()
    generate_image.main()
    verify_image.main()
    compose_card.main()
    publish_instagram.main()  # INSTAGRAM_ACCESS_TOKEN 미설정 시 내부에서 조용히 건너뜀
    check_usage.main()


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase == "collect":
        run_collect_phase()
    elif phase == "publish":
        run_publish_phase()
    else:
        print("사용법: python run_daily.py [collect|publish]")
        sys.exit(1)


if __name__ == "__main__":
    main()

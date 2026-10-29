"""일일 카드뉴스 파이프라인의 실행 진입점.

기본 자동 모드(auto)는 수집·정제·카테고리별 자동 선정·카드 생성 QA·인스타그램
업로드를 한 번에 실행한다. collect/publish는 문제 진단과 수동 재처리용으로 남긴다.
"""

import sys

import auto_select
import check_usage
import clean_news
import collect_news
import generate_cardset
import publish_instagram


def run_collect_phase() -> None:
    collect_news.main()
    clean_news.main()
    auto_select.main()


def run_publish_phase() -> None:
    generate_cardset.main()
    publish_instagram.main()
    check_usage.main()


def run_auto_phase() -> None:
    """사람 승인 없이 선정부터 QA·업로드까지 한 번에 실행한다."""
    run_collect_phase()
    run_publish_phase()


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if phase == "collect":
        run_collect_phase()
    elif phase == "publish":
        run_publish_phase()
    elif phase == "auto":
        run_auto_phase()
    else:
        print("사용법: python run_daily.py [collect|publish|auto]")
        sys.exit(1)


if __name__ == "__main__":
    main()

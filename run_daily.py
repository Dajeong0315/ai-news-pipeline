"""일일 카드뉴스 파이프라인의 실행 진입점.

기본 자동 모드(auto)는 수집·정제·카테고리별 자동 선정·카드 생성 QA·인스타그램
업로드를 한 번에 실행한다. collect/publish는 문제 진단과 수동 재처리용으로 남긴다.
각 실행은 pipeline_events에 run 시작/종료가 기록돼 대시보드에 나타난다.
"""

import sys

import auto_select
import check_usage
import clean_news
import collect_listings
import collect_news
import config
import events
import generate_cardset
import keyword_stats
import publish_instagram


def run_collect_phase() -> None:
    # 디징(youth)은 2026-10-31부터 기사 대신 공식 모집 공고를 원본으로 쓴다. keywords.json·뉴스 수집은 이 경로에서 쓰지 않는다.
    if config.CHANNEL_CONFIG.get("source") == "listings":
        collect_listings.main()
        auto_select.main()
        return
    collect_news.main()
    clean_news.main()
    auto_select.main()


def run_publish_phase() -> None:
    generate_cardset.main()
    publish_instagram.main()
    check_usage.main()


def run_cards_phase() -> None:
    """게시 없이 수집·선정·카드 생성(QA 포함)까지만. 새 채널 시험용: output/<날짜>/ 결과를 보고
    괜찮으면 같은 CHANNEL로 `python publish_instagram.py`를 실행해 올린다."""
    run_collect_phase()
    generate_cardset.main()


def run_auto_phase() -> None:
    """사람 승인 없이 선정부터 QA·업로드까지 한 번에 실행한다."""
    run_collect_phase()
    run_publish_phase()
    keyword_stats.snapshot()


PHASES = {"collect": run_collect_phase, "publish": run_publish_phase, "cards": run_cards_phase, "auto": run_auto_phase}


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if phase not in PHASES:
        print("사용법: python run_daily.py [collect|publish|cards|auto]")
        sys.exit(1)

    events.record("run", "started", message=f"{phase} 모드 시작", detail={"mode": phase, "trigger": events.TRIGGER})
    try:
        PHASES[phase]()
    except Exception as e:
        events.record("run", "failed", message=f"{type(e).__name__}: {e}")
        raise
    events.record("run", "success", message=f"{phase} 모드 완료")


if __name__ == "__main__":
    main()

"""일일 카드뉴스 파이프라인의 실행 진입점.

기본 자동 모드(auto)는 수집·정제·카테고리별 자동 선정·카드 생성 QA·인스타그램
업로드를 한 번에 실행한다. collect/publish는 문제 진단과 수동 재처리용으로 남긴다.
각 실행은 pipeline_events에 run 시작/종료가 기록돼 대시보드에 나타난다.
"""

import sys

import auto_select
import card_preview
import check_usage
import clean_news
import collect_listings
import collect_news
import config
import events
import generate_cardset
import keyword_stats
import mark_deleted_media
import publish_instagram


def check_deleted_media() -> None:
    """인스타에서 지운 게시물 표시(채널마다 하루 1번, 대시보드 "삭제됨"). 실패해도 수집은 계속한다."""
    try:
        mark_deleted_media.daily()
    except Exception as e:
        print(f"[run_daily] 삭제된 게시물 확인 건너뜀: {e}", file=sys.stderr)


def run_collect_phase() -> None:
    check_deleted_media()
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
    # 승인이 필요한 채널(디징, 경제 2026-11-02~)은 카드 생성 + 텔레그램 승인 요청까지만. 게시는 10분 작업
    # (publish_instagram.py --approved-only)이 승인된 카드만 한다.
    if not card_preview.required():
        publish_instagram.main()
    check_usage.main()


def run_cards_phase() -> None:
    """게시 없이 수집·선정·카드 생성(QA 포함)까지만. 새 채널 시험용: output/<날짜>/ 결과를 보고
    괜찮으면 같은 CHANNEL로 `python publish_instagram.py`를 실행해 올린다."""
    run_collect_phase()
    generate_cardset.main()


def run_auto_phase() -> None:
    """선정부터 QA·업로드까지 한 번에 실행한다. 승인 채널은 카드 생성·승인 요청까지(run_publish_phase)."""
    run_collect_phase()
    run_publish_phase()
    keyword_stats.snapshot()


def run_nextday_phase() -> None:
    """전날 제작(Phase 5, 17:30): 다음 날 게시 시각마다 1개 + 예비분을 만들어 승인 요청까지. 게시는 10분 작업이 예정 시각에.
    channel.json nextday가 꺼져 있으면 아무것도 안 한다(시험 채널만 켬)."""
    import schedule_plan
    from datetime import datetime, timedelta
    if not schedule_plan.enabled():
        print(f"[run_daily] {config.CHANNEL} 채널은 nextday가 꺼져 있어 전날 제작을 건너뜀")
        events.record("run", "skipped", message="nextday 꺼짐(전날 제작 안 함)")
        return
    # `nextday today`: 오늘 남은 게시 시각을 채운다(전날 제작이 한도 소진 등으로 실패했을 때 아침에 1회, 2026-11-14)
    offset = 0 if (len(sys.argv) > 2 and sys.argv[2] == "today") else 1
    tomorrow = (datetime.now(schedule_plan.KST) + timedelta(days=offset)).date()
    plan = schedule_plan.plan_for(tomorrow)
    print(f"[run_daily] {tomorrow} 채울 자리 {len(plan)}개: " + ", ".join(schedule_plan.label(p) for p in plan))
    check_deleted_media()
    if config.CHANNEL_CONFIG.get("source") == "listings":
        collect_listings.main()
    else:
        collect_news.main()
        clean_news.main()
    auto_select.main(limit=len(plan))
    generate_cardset.main(plan=plan)
    check_usage.main()
    keyword_stats.snapshot()


PHASES = {"collect": run_collect_phase, "publish": run_publish_phase, "cards": run_cards_phase, "auto": run_auto_phase,
          "nextday": run_nextday_phase}


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if phase not in PHASES:
        print("사용법: python run_daily.py [collect|publish|cards|auto|nextday]")
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

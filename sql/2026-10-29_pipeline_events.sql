-- 2026-10-29 운영 대시보드 1차: 파이프라인 단계별 이벤트 기록 테이블
-- Supabase SQL Editor에서 실행. 기존 테이블·데이터는 건드리지 않는다(추가만).

CREATE TABLE IF NOT EXISTS pipeline_events (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL,                 -- run_daily.py 1회 실행 단위
    stage         TEXT NOT NULL,                 -- run / collect / clean / select / cardset / publish / keyword_stats
    status        TEXT NOT NULL
                  CHECK (status IN ('started','success','retry','failed','skipped')),
    category      TEXT CHECK (category IN ('index_macro','stock','policy_industry')),
    count         INT,                           -- 수집 건수, 카드 장수 등
    message       TEXT,
    detail        JSONB,                         -- 키워드 집계 스냅샷, media_id 등
    news_item_id  BIGINT REFERENCES news_items(id),
    card_id       BIGINT REFERENCES cards(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_created_at ON pipeline_events(created_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_run_id ON pipeline_events(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_stage ON pipeline_events(stage, created_at);

-- 대시보드 [주제] 탭이 날짜별로 news_items를 조회하므로 추가
CREATE INDEX IF NOT EXISTS idx_news_items_collected_at ON news_items(collected_at);

-- 정책 없이 RLS만 켠다: anon/authenticated 키로는 읽기·쓰기 모두 불가,
-- service_role(파이프라인·Vercel 서버)만 접근 가능.
ALTER TABLE pipeline_events ENABLE ROW LEVEL SECURITY;

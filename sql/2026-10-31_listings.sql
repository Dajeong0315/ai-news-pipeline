-- 2026-10-31 디징(youth) 원본을 뉴스 기사 → "지금 신청 가능한 공식 모집 공고"로 바꾼다.
-- Supabase SQL Editor에서 실행. 칸만 추가하고 기존 행·제약은 건드리지 않는다(기존 행은 모두 source_type='news').
--
-- 별도 listings 테이블 대신 news_items에 칸을 붙인 이유: 선정(status 'approved')·카드(cards.news_item_id FK)·
-- 게시·대시보드가 모두 news_items 한 곳을 보고 있어서, 공고도 같은 행으로 두면 그 흐름을 그대로 탄다.
--
-- 공고 행의 기존 칸 쓰임: url = 수집처 상세 URL(채널 안 UNIQUE), source = 주최·기관명, keyword = 출처 이름(잡알리오 등),
-- summary = 대상·혜택 한 줄, published_at = 접수 시작일. 대상·혜택·지역·원문 등 나머지 정규화 값은 listing(JSONB)에 둔다.

BEGIN;

ALTER TABLE news_items
    ADD COLUMN source_type  TEXT NOT NULL DEFAULT 'news' CHECK (source_type IN ('news', 'listing')),
    ADD COLUMN apply_start  DATE,
    ADD COLUMN apply_end    DATE,
    ADD COLUMN official_url TEXT,
    ADD COLUMN listing      JSONB;  -- {source, source_id, host, target, benefit, region, listing_url, raw_text}

CREATE INDEX idx_news_items_listing_window ON news_items(channel, source_type, apply_end);

COMMIT;

-- 되돌리기:
-- BEGIN;
-- DROP INDEX idx_news_items_listing_window;
-- ALTER TABLE news_items DROP COLUMN listing, DROP COLUMN official_url, DROP COLUMN apply_end,
--     DROP COLUMN apply_start, DROP COLUMN source_type;
-- COMMIT;

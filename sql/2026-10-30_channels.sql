-- 2026-10-30 채널(인스타그램 계정) 추가: 같은 DB에 경제·IT테크 채널을 함께 기록한다.
-- Supabase SQL Editor에서 실행. 기존 행은 모두 channel='economy'가 된다. 삭제하는 데이터 없음.
--
-- 바뀌는 것
-- - news_items: channel 칸 추가, url 단독 UNIQUE → (channel, url) UNIQUE
--   (같은 기사를 경제·테크가 각자 수집할 수 있게), 카테고리 고정 목록 제약 제거
-- - pipeline_events: channel 칸 추가, 카테고리 고정 목록 제약 제거
--
-- 제약 이름은 CREATE TABLE에서 자동으로 붙은 기본 이름이다. 이름이 다르면 DROP이 오류를 내고
-- 트랜잭션 전체가 취소되므로, 일부만 적용된 상태로 남지 않는다.

BEGIN;

ALTER TABLE news_items ADD COLUMN channel TEXT NOT NULL DEFAULT 'economy';
ALTER TABLE news_items DROP CONSTRAINT news_items_url_key;
ALTER TABLE news_items ADD CONSTRAINT news_items_channel_url_key UNIQUE (channel, url);
ALTER TABLE news_items DROP CONSTRAINT news_items_category_check;

ALTER TABLE pipeline_events ADD COLUMN channel TEXT NOT NULL DEFAULT 'economy';
ALTER TABLE pipeline_events DROP CONSTRAINT pipeline_events_category_check;
CREATE INDEX idx_pipeline_events_channel ON pipeline_events(channel, created_at);

COMMIT;

-- 되돌리기(테크 행을 지운 뒤에만 가능):
-- BEGIN;
-- ALTER TABLE news_items DROP CONSTRAINT news_items_channel_url_key;
-- ALTER TABLE news_items ADD CONSTRAINT news_items_url_key UNIQUE (url);
-- ALTER TABLE news_items DROP COLUMN channel;
-- ALTER TABLE pipeline_events DROP COLUMN channel;
-- COMMIT;

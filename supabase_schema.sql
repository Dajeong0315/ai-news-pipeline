-- 경제·주식 카드뉴스 자동화 파이프라인 초기 스키마
-- Supabase SQL Editor에서 실행

CREATE TABLE news_items (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    url             TEXT UNIQUE NOT NULL,
    source          TEXT,
    category        TEXT CHECK (category IN ('index_macro','stock','policy_industry')),
    keyword         TEXT,
    summary         TEXT,
    published_at    TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ DEFAULT now(),
    dedup_group_id  BIGINT,
    status          TEXT DEFAULT 'collected'
                    CHECK (status IN ('collected','filtered','pending_approval','approved','rejected','expired'))
);

CREATE TABLE approval_requests (
    id                  BIGSERIAL PRIMARY KEY,
    news_item_id        BIGINT REFERENCES news_items(id),
    telegram_message_id BIGINT,
    sent_at             TIMESTAMPTZ DEFAULT now(),
    decision            TEXT CHECK (decision IN ('pending','approved','rejected')),
    decided_at          TIMESTAMPTZ
);

CREATE TABLE image_prompts (
    id            BIGSERIAL PRIMARY KEY,
    news_item_id  BIGINT REFERENCES news_items(id),
    prompt_text   TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE generated_images (
    id                  BIGSERIAL PRIMARY KEY,
    news_item_id        BIGINT REFERENCES news_items(id),
    image_path          TEXT,
    vision_check_passed BOOLEAN,
    vision_check_note   TEXT,
    retry_count         INT DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE cards (
    id             BIGSERIAL PRIMARY KEY,
    news_item_id   BIGINT REFERENCES news_items(id),
    category       TEXT,
    final_title    TEXT,
    image_path     TEXT,
    publish_order  INT,
    published      BOOLEAN DEFAULT false,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_news_items_status ON news_items(status);
CREATE INDEX idx_news_items_category ON news_items(category);
CREATE INDEX idx_news_items_dedup_group ON news_items(dedup_group_id);
CREATE INDEX idx_approval_requests_news_item ON approval_requests(news_item_id);

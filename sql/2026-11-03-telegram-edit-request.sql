-- 텔레그램 카드 승인에서 수정 요청 내용을 받아 다음 재생성에 전달한다.
ALTER TABLE cards ADD COLUMN IF NOT EXISTS approval TEXT DEFAULT 'pending';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS approval_chat_id BIGINT;
ALTER TABLE cards ADD COLUMN IF NOT EXISTS revision_request TEXT;
COMMENT ON COLUMN cards.revision_request IS '텔레그램에서 받은 카드 수정 요청(최대 2000자)';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS reel_only BOOLEAN DEFAULT false;

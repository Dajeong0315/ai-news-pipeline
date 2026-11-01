-- 2026-11-02 디징(youth) 카드는 운영자 승인 뒤에만 게시한다(텔레그램 미리보기 버튼·대시보드 카드 탭).
-- Supabase SQL Editor에서 실행. 칸만 추가하고 기존 행은 건드리지 않는다(기존 카드·경제 카드는 approval NULL = 승인 절차 없음).
-- 문장마다 따로 실행돼도 되고 여러 번 실행해도 안전하다(IF NOT EXISTS).
--
-- approval 값
--   pending    : 카드 생성 직후, 미리보기 보냄(운영자 결정 대기)
--   approved   : [게시] → 10분 게시 작업(publish_instagram.py --approved-only)이 게시 간격·하루 건수 안에서 올린다
--   rejected   : [건너뛰기] → 게시 안 함
--   regenerate : [다시 만들기] → 10분 작업이 같은 공고로 새 카드를 만들고 다시 승인 요청
--   replaced   : 다시 만들어져 새 카드(regenerated_from이 이 카드를 가리킴)로 대체된 옛 카드
-- 같은 공고 다시 만들기는 최대 2회(regenerated_from이 있는 카드 수로 센다).

ALTER TABLE cards ADD COLUMN IF NOT EXISTS approval TEXT;
ALTER TABLE cards ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE cards ADD COLUMN IF NOT EXISTS approved_by TEXT;           -- 텔레그램 사용자명(@...) 또는 'dashboard'
ALTER TABLE cards ADD COLUMN IF NOT EXISTS regenerated_from BIGINT REFERENCES cards(id);
ALTER TABLE cards DROP CONSTRAINT IF EXISTS cards_approval_check;
ALTER TABLE cards ADD CONSTRAINT cards_approval_check
    CHECK (approval IS NULL OR approval IN ('pending', 'approved', 'rejected', 'regenerate', 'replaced'));
CREATE INDEX IF NOT EXISTS idx_cards_approval_open ON cards(approval) WHERE published = false;

-- 확인: 아래가 네 줄이면 적용된 것
-- SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'cards'
--   AND column_name IN ('approval', 'approved_at', 'approved_by', 'regenerated_from');

-- 되돌리기:
-- BEGIN;
-- DROP INDEX idx_cards_approval_open;
-- ALTER TABLE cards DROP CONSTRAINT cards_approval_check;
-- ALTER TABLE cards DROP COLUMN regenerated_from, DROP COLUMN approved_by, DROP COLUMN approved_at, DROP COLUMN approval;
-- COMMIT;

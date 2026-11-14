-- 2026-11-14: 텔레그램 [✏️ 수정 요청] 버튼이 매번 실패한 원인 수정.
-- 11-02 제약(cards_approval_check)에 'edit_requested'가 없어 버튼을 누르면 DB가 거부(23514) → 웹훅 500.
-- Supabase SQL Editor에서 실행. 기존 행은 바뀌지 않는다.
ALTER TABLE cards DROP CONSTRAINT IF EXISTS cards_approval_check;
ALTER TABLE cards ADD CONSTRAINT cards_approval_check
    CHECK (approval IS NULL OR approval IN ('pending', 'approved', 'rejected', 'regenerate', 'replaced', 'edit_requested'));
NOTIFY pgrst, 'reload schema';

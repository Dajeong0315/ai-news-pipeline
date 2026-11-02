-- 2026-11-02 인스타에서 지운 게시물을 대시보드 카드 탭에 "삭제됨"으로 표시한다(mark_deleted_media.py가 하루 1번 확인).
-- Supabase SQL Editor에서 실행. cards.publish_status 허용 값에 'deleted'를 더한다(기존 행은 그대로, 지금 값은 published·ready뿐).
-- 여러 번 실행해도 안전하다. 적용 전에는 mark_deleted_media.py가 카드 표시만 건너뛰고 장부·이벤트 기록은 그대로 한다.
--
-- publish_status 값
--   ready      : 게시 대기(2차 설계 때 넣은 값)
--   publishing : 게시 중
--   published  : 게시됨
--   failed     : 게시 실패
--   deleted    : 게시했지만 인스타에서 지워짐(published=true는 그대로 둬서 게시 수·중복 선정 검사는 바뀌지 않음)

ALTER TABLE cards DROP CONSTRAINT IF EXISTS cards_publish_status_check;
ALTER TABLE cards ADD CONSTRAINT cards_publish_status_check
    CHECK (publish_status IS NULL OR publish_status IN ('ready', 'publishing', 'published', 'failed', 'deleted'));

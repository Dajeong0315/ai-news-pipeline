-- Phase 5 전날 제작(2026-11-14): 카드마다 게시 예정 시각과 예비분 표시.
-- scheduled_for: 이 시각이 돼야 게시(비어 있으면 옛 방식 = 승인 즉시)
-- reserve: 예비분(그 시각 카드가 승인 안 됐거나 반려면 대신 게시)
alter table cards add column if not exists scheduled_for timestamptz;
alter table cards add column if not exists reserve boolean not null default false;
create index if not exists cards_scheduled_for_idx on cards (scheduled_for) where published = false;

notify pgrst, 'reload schema';

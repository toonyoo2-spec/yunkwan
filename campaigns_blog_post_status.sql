-- 블로그 자동화 앱(blogauto) 연동: 체험단별 블로그 글 진행 상태
-- 초안완성(drafted) → 업로드완료(uploaded). 기존 행·기존 칸은 건드리지 않고 칸만 추가한다.
alter table public.campaigns
  add column if not exists post_status text,          -- null | 'drafted' | 'uploaded'
  add column if not exists post_url    text,          -- 올린 네이버 블로그 글 주소(선택)
  add column if not exists drafted_at  timestamptz,   -- 앱이 검증까지 끝낸 시각
  add column if not exists uploaded_at timestamptz;   -- 업로드완료로 표시한 시각

alter table public.campaigns
  drop constraint if exists campaigns_post_status_check;
alter table public.campaigns
  add constraint campaigns_post_status_check check (post_status is null or post_status in ('drafted', 'uploaded'));

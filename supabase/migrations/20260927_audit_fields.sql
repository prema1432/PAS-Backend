-- Audit trail for every row in every table: who created it, who last changed
-- it, and when. Columns are nullable so service-role/system inserts (seeds,
-- auth triggers) stay allowed; auth.uid() fills them for API writes.

-- ============ 1) Audit columns ============

alter table public.customers
  add column if not exists created_by uuid references auth.users (id) on delete set null,
  add column if not exists updated_by uuid references auth.users (id) on delete set null;

alter table public.profiles
  add column if not exists created_by uuid references auth.users (id) on delete set null,
  add column if not exists updated_by uuid references auth.users (id) on delete set null;

alter table public.login_events
  add column if not exists updated_at timestamptz not null default now(),
  add column if not exists created_by uuid references auth.users (id) on delete set null,
  add column if not exists updated_by uuid references auth.users (id) on delete set null;

-- ============ 2) Keep them accurate on every write ============

create or replace function public.set_audit_fields()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    new.created_at := coalesce(new.created_at, now());
    new.created_by := coalesce(new.created_by, auth.uid());
    new.updated_by := coalesce(new.updated_by, new.created_by);
  else
    -- created_* are immutable; updated_* reflect the actor of this change.
    new.created_at := old.created_at;
    new.created_by := old.created_by;
    new.updated_by := coalesce(auth.uid(), old.updated_by);
  end if;
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists set_audit_fields on public.customers;
create trigger set_audit_fields
  before insert or update on public.customers
  for each row execute function public.set_audit_fields();

drop trigger if exists set_audit_fields on public.profiles;
create trigger set_audit_fields
  before insert or update on public.profiles
  for each row execute function public.set_audit_fields();

drop trigger if exists set_audit_fields on public.login_events;
create trigger set_audit_fields
  before insert or update on public.login_events
  for each row execute function public.set_audit_fields();

-- ============ 3) Backfill existing rows ============

update public.customers
   set created_by = coalesce(created_by, user_id),
       updated_by = coalesce(updated_by, user_id)
 where created_by is null or updated_by is null;

update public.profiles
   set created_by = coalesce(created_by, id),
       updated_by = coalesce(updated_by, id)   where created_by is null or updated_by is null;

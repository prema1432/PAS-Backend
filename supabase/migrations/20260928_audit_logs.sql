-- Append-only audit trail: one row per create/update/delete on the business
-- tables, written by triggers. The app never writes to this table directly —
-- it can only read it (GET /audit-logs), so the ledger cannot be edited.

create table if not exists public.audit_logs (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  table_name text not null,
  row_id text not null,
  action text not null check (action in ('insert', 'update', 'delete')),
  changed_fields jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users (id) on delete set null
);

-- Owner-scoped reads with a plain index on the owner and recency.
create index if not exists audit_logs_user_id_idx on public.audit_logs (user_id);
create index if not exists audit_logs_user_created_idx on public.audit_logs (user_id, created_at desc);

-- RLS: owners can read their own trail; nobody writes through the API.
alter table public.audit_logs enable row level security;

drop policy if exists "users can read own audit logs" on public.audit_logs;
create policy "users can read own audit logs"
  on public.audit_logs for select to authenticated
  using (auth.uid() = user_id);

-- ============ Trigger: record changes on the business tables ============

create or replace function public.write_audit_log()
returns trigger
language plpgsql
security definer set search_path = public
as $$
declare
  changed text[] := '{}';
  col text;
begin
  if tg_op = 'DELETE' then
    insert into public.audit_logs (user_id, table_name, row_id, action, changed_fields)
    values (
      old.user_id,
      tg_table_name,
      coalesce(old.id::text, ''),
      'delete',
      to_jsonb(array(select key from jsonb_each(to_jsonb(old))))
    );
    return old;
  end if;

  if tg_op = 'UPDATE' then
    for col in select jsonb_object_keys(to_jsonb(new)) loop
      if (to_jsonb(new) -> col) is distinct from (to_jsonb(old) -> col) then
        changed := changed || col;
      end if;
    end loop;
    if changed = '{}' then
      return new;  -- nothing actually changed; do not log no-op updates
    end if;
  end if;

  insert into public.audit_logs (user_id, table_name, row_id, action, changed_fields)
  values (
    new.user_id,
    tg_table_name,
    coalesce(new.id::text, ''),
    lower(tg_op),
    to_jsonb(changed)
  );
  return coalesce(new, old);
end;
$$;

do $$
declare
  target text;
begin
  foreach target in array array[
    'customers', 'providers', 'provider_models', 'api_keys', 'payments', 'customer_sessions'
  ]
  loop
    execute format('drop trigger if exists write_audit_log on public.%I', target);
    execute format(
      'create trigger write_audit_log after insert or update or delete on public.%I for each row execute function public.write_audit_log()',
      target
    );
  end loop;
end $$;

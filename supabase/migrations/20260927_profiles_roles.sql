-- User profiles with roles. Roles are never exposed in the UI; the default
-- admin account holds the hidden "CEO" role, everyone else is "customer".

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  role text not null default 'customer'
    check (role in ('customer', 'support', 'admin', 'CEO')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Every new auth user automatically gets a profile with the default role.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email, role)
  values (new.id, new.email, 'customer')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Backfill: every pre-existing user becomes a "customer".
insert into public.profiles (id, email, role)
select id, email, 'customer' from auth.users
on conflict (id) do nothing;

-- RLS: users may read their own profile (role included server-side only).
alter table public.profiles enable row level security;

drop policy if exists "users can read own profile" on public.profiles;
create policy "users can read own profile"
  on public.profiles for select to authenticated
  using (auth.uid() = id);

-- No client-side insert/update policies: roles change only via service role.

create index if not exists profiles_role_idx on public.profiles (role);

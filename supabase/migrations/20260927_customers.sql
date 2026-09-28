-- Customers managed from the app sidebar. Each row belongs to the signed-in
-- user (user_id) and carries a 6-digit OTP, an active flag and a plan.

create table if not exists public.customers (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 120),
  email text not null check (position('@' in email) > 1),
  phone text not null check (char_length(phone) between 7 and 20),
  otp char(6) not null check (otp ~ '^[0-9]{6}$'),
  is_active boolean not null default true,
  plan text not null default 'free' check (plan in ('free', 'paid')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One customer per email address, per owner.
create unique index if not exists customers_user_email_key
  on public.customers (user_id, lower(email));

create index if not exists customers_user_id_idx on public.customers (user_id);

-- RLS: owners only.
alter table public.customers enable row level security;

drop policy if exists "users can read own customers" on public.customers;
drop policy if exists "users can insert own customers" on public.customers;
drop policy if exists "users can update own customers" on public.customers;
drop policy if exists "users can delete own customers" on public.customers;

create policy "users can read own customers"
  on public.customers for select to authenticated
  using (auth.uid() = user_id);

create policy "users can insert own customers"
  on public.customers for insert to authenticated
  with check (auth.uid() = user_id);

create policy "users can update own customers"
  on public.customers for update to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "users can delete own customers"
  on public.customers for delete to authenticated
  using (auth.uid() = user_id);

-- Provider catalogue (OpenRouter, OpenAI, ...), their models, the encrypted API
-- keys used to call them, a payments ledger and per-customer sessions.
--
-- Every table is owner-scoped (user_id) with RLS enabled, and carries the four
-- audit columns maintained by public.set_audit_fields().

-- ============ 1) Providers ============
-- priority orders the preference list (lower = tried first); is_free/is_paid
-- record which tiers the provider can serve.

create table if not exists public.providers (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 80),
  slug text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  base_url text,
  is_free boolean not null default true,
  is_paid boolean not null default false,
  preferred_tier text not null default 'free' check (preferred_tier in ('free', 'paid')),
  priority integer not null default 100,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  updated_by uuid references auth.users (id) on delete set null
);

create unique index if not exists providers_user_slug_key
  on public.providers (user_id, slug);

create index if not exists providers_user_priority_idx
  on public.providers (user_id, priority);

-- ============ 2) Provider models ============

create table if not exists public.provider_models (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  provider_id bigint not null references public.providers (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 160),
  is_free boolean not null default true,
  is_paid boolean not null default false,
  context_window integer check (context_window is null or context_window > 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  updated_by uuid references auth.users (id) on delete set null
);

create unique index if not exists provider_models_provider_name_key
  on public.provider_models (provider_id, name);

create index if not exists provider_models_provider_idx
  on public.provider_models (provider_id);

-- ============ 3) API keys (encrypted at rest) ============
-- key_ciphertext holds the Fernet token; the plaintext key is never stored and
-- never returned. key_hint keeps the last 4 characters so the UI can identify a
-- key without revealing it.

create table if not exists public.api_keys (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  provider_id bigint references public.providers (id) on delete cascade,
  label text not null check (char_length(label) between 1 and 60),
  key_ciphertext text not null,
  key_hint text not null check (char_length(key_hint) <= 8),
  tier text not null default 'free' check (tier in ('free', 'paid', 'any')),
  is_active boolean not null default true,
  last_used_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  updated_by uuid references auth.users (id) on delete set null
);

create index if not exists api_keys_user_idx on public.api_keys (user_id);
create index if not exists api_keys_provider_idx on public.api_keys (provider_id);

-- ============ 4) Customers: remaining time + auto-recharge ============
-- The balance is authoritative (stored) rather than derived, so the customer
-- table can show the remaining minutes without an aggregate query.

alter table public.customers
  add column if not exists remaining_minutes integer not null default 0,
  add column if not exists auto_recharge boolean not null default false,
  add column if not exists auto_recharge_amount numeric(10, 2) not null default 0,
  add column if not exists auto_recharge_minutes integer not null default 0;

do $$
begin
  alter table public.customers
    add constraint customers_remaining_minutes_non_negative check (remaining_minutes >= 0),
    add constraint customers_auto_recharge_amount_non_negative check (auto_recharge_amount >= 0),
    add constraint customers_auto_recharge_minutes_non_negative check (auto_recharge_minutes >= 0);
exception
  when duplicate_object then null;
end $$;

-- ============ 5) Payments / recharges ============
-- mode tells an automatic top-up apart from a manually recorded payment.

create table if not exists public.payments (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  customer_id bigint not null references public.customers (id) on delete cascade,
  amount numeric(10, 2) not null default 0 check (amount >= 0),
  currency char(3) not null default 'INR',
  minutes integer not null default 0 check (minutes >= 0),
  mode text not null default 'manual' check (mode in ('auto', 'manual')),
  status text not null default 'paid' check (status in ('paid', 'pending', 'failed')),
  reference text,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  updated_by uuid references auth.users (id) on delete set null
);

create index if not exists payments_user_created_idx
  on public.payments (user_id, created_at desc);

create index if not exists payments_customer_idx
  on public.payments (customer_id);

-- ============ 6) Per-customer sessions ============
-- One row per customer session; minutes_used is charged against the customer's
-- remaining_minutes when the session ends.

create table if not exists public.customer_sessions (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  customer_id bigint not null references public.customers (id) on delete cascade,
  provider_id bigint references public.providers (id) on delete set null,
  model text,
  status text not null default 'active' check (status in ('active', 'ended')),
  minutes_used integer not null default 0 check (minutes_used >= 0),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid references auth.users (id) on delete set null,
  updated_by uuid references auth.users (id) on delete set null
);

create index if not exists customer_sessions_user_started_idx
  on public.customer_sessions (user_id, started_at desc);

create index if not exists customer_sessions_customer_idx
  on public.customer_sessions (customer_id);

-- ============ 7) RLS: owners only ============
alter table public.providers enable row level security;
alter table public.provider_models enable row level security;
alter table public.api_keys enable row level security;
alter table public.payments enable row level security;
alter table public.customer_sessions enable row level security;

do $$
declare
  target text;
begin
  foreach target in array array[
    'providers', 'provider_models', 'api_keys', 'payments', 'customer_sessions'
  ]
  loop
    execute format('drop policy if exists "users can read own %1$s" on public.%1$I', target);
    execute format('drop policy if exists "users can insert own %1$s" on public.%1$I', target);
    execute format('drop policy if exists "users can update own %1$s" on public.%1$I', target);
    execute format('drop policy if exists "users can delete own %1$s" on public.%1$I', target);

    execute format(
      'create policy "users can read own %1$s" on public.%1$I for select to authenticated using (auth.uid() = user_id)',
      target
    );
    execute format(
      'create policy "users can insert own %1$s" on public.%1$I for insert to authenticated with check (auth.uid() = user_id)',
      target
    );
    execute format(
      'create policy "users can update own %1$s" on public.%1$I for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id)',
      target
    );
    execute format(
      'create policy "users can delete own %1$s" on public.%1$I for delete to authenticated using (auth.uid() = user_id)',
      target
    );
  end loop;
end $$;

-- ============ 8) Audit triggers ============

do $$
declare
  target text;
begin
  foreach target in array array[
    'providers', 'provider_models', 'api_keys', 'payments', 'customer_sessions'
  ]
  loop
    execute format('drop trigger if exists set_audit_fields on public.%I', target);
    execute format(
      'create trigger set_audit_fields before insert or update on public.%I for each row execute function public.set_audit_fields()',
      target
    );
  end loop;
end $$;

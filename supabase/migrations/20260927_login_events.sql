-- One row per successful sign-in (and account creation), capturing everything
-- the request can tell us about where and how the user connected.

create table if not exists public.login_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,

  -- Network
  ip text,
  ip_version text check (ip_version in ('IPv4', 'IPv6', 'unknown')),
  forwarded_for text,

  -- Device / client (parsed from the User-Agent header)
  user_agent text,
  browser text,
  browser_version text,
  os text,
  os_version text,
  device_type text,          -- Desktop | Mobile | Tablet | Bot | Unknown
  is_bot boolean not null default false,

  -- Approximate location (from IP geolocation; null when unavailable)
  city text,
  region text,
  country text,
  country_code text,
  continent text,
  postal text,
  latitude double precision,
  longitude double precision,
  timezone text,
  isp text,

  -- Request context
  language text,
  referer text,
  origin text,
  accept_encoding text,
  method text,
  path text,
  created_at timestamptz not null default now()
);

create index if not exists login_events_user_id_idx
  on public.login_events (user_id, created_at desc);

alter table public.login_events enable row level security;

drop policy if exists "users can read own login events" on public.login_events;
drop policy if exists "users can insert own login events" on public.login_events;

create policy "users can read own login events"
  on public.login_events for select to authenticated
  using (auth.uid() = user_id);

create policy "users can insert own login events"
  on public.login_events for insert to authenticated
  with check (auth.uid() = user_id);

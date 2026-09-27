-- Create the todos table used by the PAS Backend /todos API.
-- Run in the Supabase dashboard SQL Editor, or via: supabase db push

create table if not exists public.todos (
  id bigint generated always as identity primary key,
  title text not null check (char_length(title) between 1 and 200),
  completed boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- RLS: the API uses the anon key, so anon must be allowed to work with todos.
alter table public.todos enable row level security;

create policy "anon can read todos"
  on public.todos for select to anon using (true);

create policy "anon can insert todos"
  on public.todos for insert to anon with check (true);

create policy "anon can update todos"
  on public.todos for update to anon using (true) with check (true);

create policy "anon can delete todos"
  on public.todos for delete to anon using (true);

-- Phone numbers and emails must be unique across the whole customers table —
-- no two owners may hold the same contact details. Phone is normalised to
-- digits only, so "+91 98765 43210" and "919876543210" count as the same
-- number; email is compared case-insensitively.

create unique index if not exists customers_phone_key
  on public.customers (regexp_replace(phone, '[^0-9]', '', 'g'));

create unique index if not exists customers_email_key
  on public.customers (lower(email));

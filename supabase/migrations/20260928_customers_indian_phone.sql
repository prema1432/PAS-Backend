-- Indian mobile numbers only: exactly 10 digits, first digit 6–9. The app
-- stores every number canonically as "+91XXXXXXXXXX" (see
-- app/modules/customers/schemas.py, normalize_phone).

alter table public.customers drop constraint if exists customers_phone_indian_mobile;

alter table public.customers
  add constraint customers_phone_indian_mobile
  check (phone ~ '^\+91[6-9][0-9]{9}$');

-- ============================================================
-- ParkFlow Kenya — Supabase PostgreSQL schema
-- Project: pjpqufjuaagnavlfdmew.supabase.co
-- Run this file in: Supabase Dashboard → SQL Editor → New query
-- Then run: Run (⌘/Ctrl + Enter)
-- Idempotent: safe to run more than once.
-- ============================================================

-- ------------------------------------------------------------
-- 1. parking_slots
-- ------------------------------------------------------------
create table if not exists public.parking_slots (
  id          bigint generated always as identity primary key,
  slot_number varchar(10)  not null unique,
  status      varchar(20)  not null default 'AVAILABLE'
              check (status in ('AVAILABLE', 'OCCUPIED', 'MAINTENANCE',
                                'RESERVED', 'OUT_OF_SERVICE')),
  location    varchar(100) not null default '',
  created_at  timestamptz  not null default now(),
  updated_at  timestamptz  not null default now()
);

-- ------------------------------------------------------------
-- 2. vehicles
-- ------------------------------------------------------------
create table if not exists public.vehicles (
  id                 bigint generated always as identity primary key,
  registration_number varchar(12) not null unique,
  vehicle_type       varchar(20) not null default 'CAR'
                     check (vehicle_type in ('CAR', 'MOTORCYCLE', 'TRUCK',
                                             'BUS', 'OTHER')),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

-- Normalise plate format on write: 'kaa123a' -> 'KAA 123A'.
-- Must match VehicleRegistry.normalize_registration (Flask) and
-- Vehicle.save() (Django): strip whitespace, uppercase, then insert a
-- space after the 3rd character when the value is >= 4 chars.
create or replace function public.normalize_registration()
returns trigger as $$
begin
  new.registration_number := upper(regexp_replace(trim(new.registration_number), '\s+', '', 'g'));
  if length(new.registration_number) >= 4 then
    new.registration_number :=
      left(new.registration_number, 3) || ' ' || substr(new.registration_number, 4);
  end if;
  new.updated_at := now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists vehicles_normalize_registration on public.vehicles;
create trigger vehicles_normalize_registration
  before insert or update on public.vehicles
  for each row execute function public.normalize_registration();

-- ------------------------------------------------------------
-- 3. parking_sessions
-- ------------------------------------------------------------
create table if not exists public.parking_sessions (
  id              bigint generated always as identity primary key,
  vehicle_id      bigint not null references public.vehicles (id) on delete cascade,
  slot_id         bigint not null references public.parking_slots (id) on delete restrict,
  entry_time      timestamptz not null default now(),
  exit_time       timestamptz,
  duration_minutes integer not null default 0 check (duration_minutes >= 0),
  amount_due      numeric(10,2) not null default 0 check (amount_due >= 0),
  status          varchar(20) not null default 'ACTIVE'
                  check (status in ('ACTIVE', 'COMPLETED', 'CANCELLED')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- Integrity: a vehicle can have at most one ACTIVE session
create unique index if not exists parking_sessions_one_active_per_vehicle
  on public.parking_sessions (vehicle_id) where status = 'ACTIVE';

create index if not exists parking_sessions_status_idx
  on public.parking_sessions (status);
create index if not exists parking_sessions_entry_time_idx
  on public.parking_sessions (entry_time desc);

-- ------------------------------------------------------------
-- 4. payments
-- ------------------------------------------------------------
create table if not exists public.payments (
  id                   bigint generated always as identity primary key,
  session_id           bigint not null references public.parking_sessions (id) on delete cascade,
  amount               numeric(10,2) not null check (amount >= 0),
  -- Cash only for now; widen this CHECK when M-Pesa/card are configured
  payment_method       varchar(20) not null default 'CASH'
                       check (payment_method in ('CASH')),
  payment_status       varchar(20) not null default 'PAID'
                       check (payment_status in ('PENDING', 'PAID', 'FAILED', 'REFUNDED')),
  transaction_reference varchar(100) not null unique,
  payment_time         timestamptz not null default now(),
  created_at           timestamptz not null default now()
);

create index if not exists payments_session_idx
  on public.payments (session_id);

-- ------------------------------------------------------------
-- 5. parking_rates (mirrors the fee rules in the client spec)
-- ------------------------------------------------------------
create table if not exists public.parking_rates (
  id                bigint generated always as identity primary key,
  vehicle_type      varchar(20) not null
                    check (vehicle_type in ('CAR', 'MOTORCYCLE', 'TRUCK',
                                            'BUS', 'OTHER')),
  free_minutes      integer      not null default 30,
  two_hour_rate     numeric(10,2) not null default 50,   -- 31–120 min
  four_hour_rate    numeric(10,2) not null default 100,  -- 121–240 min
  six_hour_rate     numeric(10,2) not null default 300,  -- 241–360 min
  over_six_hour_rate numeric(10,2) not null default 500, -- 361+ min
  active            boolean      not null default true,
  effective_from    timestamptz  not null default now(),
  created_at        timestamptz  not null default now(),
  updated_at        timestamptz  not null default now()
);

-- One active rate row per vehicle type
create unique index if not exists parking_rates_one_active_per_type
  on public.parking_rates (vehicle_type) where active;

-- ------------------------------------------------------------
-- 6. updated_at maintenance
-- ------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at := now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists parking_slots_set_updated_at on public.parking_slots;
create trigger parking_slots_set_updated_at
  before update on public.parking_slots
  for each row execute function public.set_updated_at();

drop trigger if exists parking_sessions_set_updated_at on public.parking_sessions;
create trigger parking_sessions_set_updated_at
  before update on public.parking_sessions
  for each row execute function public.set_updated_at();

drop trigger if exists parking_rates_set_updated_at on public.parking_rates;
create trigger parking_rates_set_updated_at
  before update on public.parking_rates
  for each row execute function public.set_updated_at();

-- vehicles updated_at handled by normalize_registration trigger above.

-- ------------------------------------------------------------
-- 7. Row Level Security — ENABLED on every table, deliberate
--    policies below (RLS is never disabled).
--
--    Design: only the Django/Flask servers call Supabase with the
--    publishable key (.env, never sent to the browser). Supabase
--    cannot distinguish a server-side anon caller from any other
--    anon caller, so the prototype policy grants anon full CRUD.
--    Production hardening: store the SERVICE-ROLE key in the
--    server's .env only (browser never receives it), then replace
--    the policies below with "to authenticated" policies.
-- ------------------------------------------------------------
alter table public.parking_slots  enable row level security;
alter table public.vehicles       enable row level security;
alter table public.parking_sessions enable row level security;
alter table public.payments       enable row level security;
alter table public.parking_rates  enable row level security;

do $$
declare t text;
begin
  foreach t in array array['parking_slots', 'vehicles', 'parking_sessions',
                           'payments', 'parking_rates']
  loop
    execute format('drop policy if exists "%1$s_server_full_access" on public.%1$s', t);
    execute format(
      'create policy "%1$s_server_full_access" on public.%1$s
         for all to anon, authenticated
         using (true) with check (true)', t);
  end loop;
end;
$$;

-- ------------------------------------------------------------
-- 8. Seed data — 20 slots + default CAR rates
-- ------------------------------------------------------------
insert into public.parking_slots (slot_number, location)
select 'A' || lpad(i::text, 2, '0'), 'Block A - Main'
from generate_series(1, 15) i
on conflict (slot_number) do nothing;

insert into public.parking_slots (slot_number, location)
select 'B' || lpad(i::text, 2, '0'), 'Block B - Overflow'
from generate_series(1, 5) i
on conflict (slot_number) do nothing;

-- Normalise slot locations to plain ASCII (older seeds used an em dash)
update public.parking_slots set location = 'Block A - Main'
  where location like 'Block A%';
update public.parking_slots set location = 'Block B - Overflow'
  where location like 'Block B%';

insert into public.parking_rates (vehicle_type)
values ('CAR')
on conflict do nothing;

-- ------------------------------------------------------------
-- 9. Re-normalise existing vehicle rows (runs the fixed trigger)
-- ------------------------------------------------------------
update public.vehicles set updated_at = now();

-- ============================================================
-- Done. Verify with:
--   select slot_number, status from parking_slots order by slot_number;
--   select registration_number from vehicles;
--   select * from parking_rates;
-- ============================================================

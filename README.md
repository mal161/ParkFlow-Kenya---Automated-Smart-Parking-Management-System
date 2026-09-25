# ParkFlow Kenya — Automated Smart Parking Management System

An automated parking management system built as a data-structures-and-algorithms
academic project. It handles real-time slot allocation, entry/exit tracking,
automated fee calculation, cash payment processing, and reporting.

## Architecture

```
Browser (HTML/JS)
    │  HTTP
    ▼
Django web app  (parkflow/, port 8000)
    │  JSON proxy + best-effort local mirror
    ▼
Flask API  (flask_api/, port 5001)   ← all algorithms & business rules
    │  write-through persistence
    ▼
Supabase PostgreSQL  (source of truth)
```

- **Supabase PostgreSQL** is the source of truth (slots, vehicles, sessions,
  payments, rates — see `docs/database.sql`).
- **Flask** holds the live working state in memory (registries, index, queue)
  and synchronises mutations to Supabase through a diff-based `_sync()`.
- **Django** is a thin proxy layer: it validates input, calls the Flask API,
  mirrors results into a **local SQLite reporting replica**
  (`parkflow/db.sqlite3`) for list/report pages, and renders the UI.
- If Flask is down, Django responds with `503 Parking engine unavailable…`.
  If Supabase is unreachable at startup, Flask logs a warning and runs
  in-memory (20-slot default layout is seeded).

## Features

- Live slot map (Block A – Main, Block B – Overflow) with occupancy status
- Vehicle entry → automatic slot allocation (first-fit or nearest-to-entrance)
- Vehicle exit → duration + fee calculation (ceiling rounding)
- Cash payment processing with amount validation; exit barrier logic
- FIFO waiting queue when the lot is full (served on every slot release)
- Vehicle registration & lookup (normalised plates: `KAA 123A`)
- Dashboard (occupancy, revenue, active sessions), payments list,
  vehicle history, and 5 report types (daily, revenue, occupancy,
  vehicle-history, payment-summary)
- Django admin for master data

## Fee schedule (per vehicle type, ceiling duration rounding)

| Duration | CAR / OTHER | MOTORCYCLE | TRUCK | BUS |
|---|---|---|---|---|
| 0–30 min | KSh 0 | KSh 0 | KSh 0 | KSh 0 |
| 31–120 min | 50 | 20 | 100 | 150 |
| 121–240 min | 100 | 50 | 200 | 300 |
| 241–360 min | 300 | 100 | 500 | 800 |
| 361+ min | 500 | 200 | 1000 | 1500 |

Example: 30m01s rounds up to 31 minutes → KSh 50.

**Workflow:** exit records time/duration/fee. If the fee is 0 the session
completes and the slot is released immediately. If the fee is > 0 the slot
stays occupied until payment; the payment completes the session, releases the
slot, and serves the next queued vehicle. Only `CASH` payments are enabled
(M-Pesa/card are planned).

## Tech stack

- Python 3.14, Django 6.1.1, Flask 3.1.3, flask-cors, supabase 2.31.0,
  python-dotenv (pinned in `requirements.txt`)
- SQLite (local reporting replica), Supabase PostgreSQL (source of truth)

## Setup

```powershell
# 1. Virtualenv + dependencies
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Environment
copy .env.example .env        # then fill in SUPABASE_URL / SUPABASE_KEY
                              # (optional: DJANGO_SECRET_KEY, DEBUG)

# 3. Database schema
#    - Run the contents of docs/database.sql in the Supabase SQL editor
#      (creates 5 tables, RLS policies, triggers, 20 seeded slots, rates)
#    - Local reporting replica:
.venv\Scripts\python.exe parkflow\manage.py migrate
```

## Running

Two terminals from the project root:

```powershell
# Terminal 1 — Flask parking engine (port 5001)
python run_flask.py

# Terminal 2 — Django web app (port 8000)
python parkflow\manage.py runserver
```

Open http://127.0.0.1:8000/ — the root redirects to the operator dashboard.
Admin: http://127.0.0.1:8000/admin/

## Testing

```powershell
# Unit/integration tests (33 tests)
python parkflow\manage.py test tests -v 1

# Full-stack end-to-end check (starts its own Flask, exercises
# entry → exit → payment → mirror → pages against live Supabase)
python e2e_check.py
```

## Data structures & algorithms

All implemented in `flask_api/data_structures/` and `flask_api/algorithms/`:

**Data structures**

| Structure | Implementation | Purpose | Complexity |
|---|---|---|---|
| `ParkingSlotRegistry` | Hash map keyed by slot number | O(1) slot lookup, status counts | lookup/update O(1) |
| `VehicleRegistry` | Hash map keyed by normalised plate | Register/find vehicles | O(1) average |
| `SessionIndex` | Hash map (session id → session) + per-vehicle index | Active session lookup, duplicate-entry detection | O(1) average |
| `WaitingQueue` | Linked queue (FIFO) | Hold vehicles when lot is full | enqueue/dequeue O(1) |

**Algorithms**

| Algorithm | Description | Complexity |
|---|---|---|
| `calculate_parking_fee` | Tiered fee lookup from the rate table | O(1) |
| `calculate_duration_minutes` | Ceiling division of elapsed time | O(1) |
| `FirstFitAllocation` | First available slot in registry order | O(n) slots scan |
| `NearestToEntranceAllocation` | Slot with the lowest entrance distance | O(n) slots scan |
| `process_vehicle_entry` | Validate → allocate → create session (queued if full) | O(n) |
| `process_vehicle_exit` | Record exit, compute duration/fee | O(1) |
| `process_payment` | Validate amount/method, mark paid | O(1) |
| `check_availability` | Slot status counts + occupancy rate | O(n) |
| `authorize_exit_barrier` | Opens only when payment is settled (or fee = 0) | O(1) |

**Persistence design:** in-memory state is the working set; `_sync()` diffs
it against Supabase and writes only what changed. Failures raise
`PersistenceError` and callers roll back memory (with compensating deletes)
so memory and database stay consistent. Session ids are re-keyed to the
database id after insert.

## Project structure

```
parkflow/            Django project
  config/            settings, root URLs
  common/            Flask HTTP client + SQLite mirroring
  dashboard/         operator dashboard + slot grid
  parking/           entry/exit/fee proxies, slot & session models
  vehicles/          lookup/register proxies, vehicle model
  payments/          fee/payment proxies, payment model & list
  reports/           5 JSON report endpoints + report page
  templates/  static/
flask_api/           Flask engine
  app.py             REST API (/api/v1/...)
  algorithms/        fee, allocation, entry/exit/payment workflows
  data_structures/   registries, session index, waiting queue
  services/          ParkingService (orchestration + _sync) , SupabaseService
docs/database.sql    Supabase schema, RLS, triggers, seed data
tests/               Django test suite (33 tests)
e2e_check.py         full-stack end-to-end script
run_flask.py         Flask runner (port 5001)
```

## Security notes

- `.env` is gitignored; `.env.example` documents the variables.
- Django `SECRET_KEY` / `DEBUG` / `ALLOWED_HOSTS` are env-overridable
  (insecure defaults are for local development only).
- CSRF: `base.html` renders the token so Django sets the `csrftoken` cookie;
  `base.js` sends it as the `X-CSRFToken` header on every `fetch()` POST.
- Flask CORS is restricted to the local Django origin; browsers never call
  Flask directly (Django proxies server-to-server).
- Supabase: RLS is enabled on all tables. The key in `.env` is the publishable
  (anon) key — acceptable only because the API is server-side and local.
  For production use a service-role key on the server, restrict the anon
  policies to `authenticated`, and never ship either key to a browser.

## Known limitations

- **No cross-row transactions:** PostgREST calls are individual HTTP requests,
  so multi-row writes are compensated best-effort (rollback + compensating
  deletes) rather than atomic. A partial failure is logged, not hidden.
- **Waiting queue is in-memory/transient:** a Flask restart empties the queue
  (completed/active sessions are restored from Supabase, queued waiters are not).
- **Reporting replica is best-effort:** report/list pages read the local
  SQLite mirror, which is refreshed when the dashboard loads. Start the
  dashboard first to sync.
- **SQL trigger fix pending:** `docs/database.sql` normalises plates to
  `KAA 123A` (space after 3rd character). If your Supabase project still has
  the older trigger, re-run `docs/database.sql` in the SQL editor; the API
  accepts both formats until then.
- **No authentication on app pages** beyond Django admin; the system assumes
  a trusted local network (typical for an academic demo).
- Cash-only payments; M-Pesa/card integration is future work.

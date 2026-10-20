# INDIGO WORKFORCE

Standalone workforce scheduling, attendance and labour-cost application for
security companies (temporary, configurable product name — `INDIGO_PRODUCT_NAME`).
Greek user interface.

**Status: pilot candidate on synthetic data — not production-ready.** Payroll
figures are **estimates** based on unverified, configurable rules and require
accountant review. See [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## What it does

| Area | Implemented |
|---|---|
| Companies & users | Company accounts with strict server-side data isolation; roles: company administrator, scheduling manager, payroll operator, read-only reviewer |
| Employees | Employment dates, contract type/hours, working-time limits, minimum rest, consecutive-day limit, qualifications/licences with expiry, weekly unavailability, absences (leave/sick/unavailable), site eligibility/prohibition/preference, night/weekend preferences, fixed posts, relief staff, effective-dated compensation |
| Clients & sites | Clients, contracts with active period, sites, coverage requirements (weekdays, time window incl. overnight/24h, split into shifts, simultaneous headcount, required qualifications, holiday rule, validity, billing rate) |
| Scheduling | OR-Tools CP-SAT engine: maximise coverage first (lexicographic), then weighted soft objectives; locked shifts; regenerate selected dates/sites; preview/diff; manual editing with validation; versioning & publication; optimistic locking against concurrent edits |
| Gaps | Best partial schedule, uncovered slots with blocker analysis and cause (no qualified staff / unavailable / capacity), missing hours, staffing-capacity check; proven-maximum vs time-limit is reported explicitly |
| Disruptions | Absences after publication → affected shifts → ranked replacement candidates with reasons and effects (hours, rest, cost) → staged in draft → manager publishes |
| Attendance | Separate planned / actual / approved records; append-only corrections with reasons; reconciliation with discrepancy flags |
| Payroll & cost (estimate) | Regular, night, Sunday/holiday, part-time extra, overwork, overtime; contractual vs statutory (max applied); employer contributions; cost per employee/site/shift/month; planned vs actual; margin; versioned runs; period lock & audited reopen; accountant CSV export |
| Reports | Coverage, gaps, hours, costs; CSV exports (UTF-8 BOM, `;`) |
| Operations | Audit log, Argon2 passwords, revocable sessions, CSRF, security headers, PII-scrubbed logs with error ids, SQLite or PostgreSQL, additive migrations, backup/restore instructions |

## Quick start (development / pilot on one machine)

Requires Python 3.12+ (tested on 3.13).

```bash
cd indigo-workforce
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

export INDIGO_DATABASE_URL=sqlite:///var/indigo.sqlite3
export INDIGO_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")

python -m indigo.cli migrate
# synthetic demo company with one user per role (password asked, or INDIGO_DEMO_PASSWORD)
python -m indigo.cli seed-demo 2026 11
uvicorn --factory indigo.web.app:create_app --port 8000
# open http://localhost:8000 → admin@demo.invalid / scheduler@… / payroll@… / reviewer@…
```

Real company (no demo data):

```bash
INDIGO_ADMIN_PASSWORD='…' python -m indigo.cli create-company "Επωνυμία" admin@example.gr "Ονοματεπώνυμο"
```

Scripted pilot workflow on synthetic CSVs (import → schedule → publish → absence →
replacement → attendance → reconciliation → payroll estimate → exports):

```bash
python -m indigo.cli pilot 2026 11      # exports in var/pilot_<id>/
```

## Tests

```bash
pytest                                   # SQLite
INDIGO_TEST_DATABASE_URL=postgresql+psycopg://user@host:port/indigo_test pytest   # PostgreSQL (DB is wiped)
```

Recorded results: [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – structure, data model, scheduling model, time & payroll rules
* [docs/FEATURES.md](docs/FEATURES.md) – feature inventory with actual status
* [docs/LIMITATIONS.md](docs/LIMITATIONS.md) – known limitations and release blockers
* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) – deployment, backup/restore, rollback
* [docs/ROADMAP.md](docs/ROADMAP.md) – implementation roadmap
* [docs/PILOT.md](docs/PILOT.md) – customer pilot procedure
* [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md) – reused code and licences
* [HANDOFF.md](HANDOFF.md) – exact next steps

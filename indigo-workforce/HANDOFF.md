# HANDOFF — INDIGO WORKFORCE

Branch: `claude/indigo-workforce-extraction-c178el` · Product code: `indigo-workforce/`
(original repository files at the root are untouched).

## State in one paragraph

A working standalone application (FastAPI + SQLAlchemy + OR-Tools CP-SAT,
Greek server-rendered UI) covering employees, sites/demand, automatic
scheduling with gap explanations, versioned publication, absences and
replacements, attendance/approval reconciliation, payroll/cost **estimates**
with locking, reports/exports, roles, company isolation and audit. 66 tests
pass on SQLite and PostgreSQL; a scripted pilot runs end to end on synthetic
data. SECURITY CENTER source was not available, so nothing was extracted from it.

## Run it

```bash
cd indigo-workforce && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export INDIGO_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
INDIGO_DEMO_PASSWORD='demo-password-2026' python -m indigo.cli seed-demo 2026 11
uvicorn --factory indigo.web.app:create_app --port 8000      # admin@demo.invalid / demo-password-2026
pytest                                                       # 66 tests
python -m indigo.cli pilot 2026 11                           # scripted pilot
```

## Exact next steps

1. **Owner:** grant access to the SECURITY CENTER repository (add it to the
   session / GitHub app) if any of its scheduling or payroll logic should be
   compared or reused; then diff its rules against `indigo/engine/rules.py`
   and `indigo/payroll_engine.py` and record differences in docs/FEATURES.md.
2. **Owner + accountant:** supply authoritative payroll parameters (collective
   agreement for security staff, EFKA rates, overtime rules) with sources;
   enter them via Ρυθμίσεις → Παράμετροι μισθοδοσίας with "verified" + sources,
   and add hand-checked test cases for them in `tests/test_payroll_engine.py`.
3. **Owner:** decide product name, licence, and pilot hosting (PostgreSQL + TLS);
   follow docs/DEPLOYMENT.md; build the Docker image on the host (unverified here) and run a restore test.
4. Run the hands-on pilot (docs/PILOT.md) with the customer's scheduling manager using
   synthetic or explicitly authorised data; log every missing/incorrect rule.
5. Engineering backlog in priority order (docs/ROADMAP.md): background job queue
   for generation; partial-day absences; weekly-rest rule; daily overtime/caps once verified;
   per-IP rate limiting; XLSX/PDF exports.

## Where things are

* Hard rules: `indigo/engine/rules.py` (validator) and `indigo/engine/solver.py` (same rules as CP-SAT constraints) — change both together; invariant tests in `tests/test_engine.py` re-validate solver output.
* Workflows: `indigo/services/*.py`; routes: `indigo/web/routes/*.py`; templates: `indigo/web/templates/`.
* Permissions: `indigo/authz.py` (every route uses `need("…")`).
* Schema changes: add a new entry to `MIGRATIONS` in `indigo/migrations.py` (additive only).

## Cautions

* Do not present payroll numbers as official; every rule set is "ΜΗ επαληθευμένο" until verified.
* Tests wipe the database in `INDIGO_TEST_DATABASE_URL` — never point it at real data.
* No credentials or customer data are in the repository; keep it that way (`.env` is git-ignored).

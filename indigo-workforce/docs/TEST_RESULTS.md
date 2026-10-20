# Test results (recorded)

Environment: Linux container, Python 3.13.16, OR-Tools 9.14.6206, SQLAlchemy 2.1.5,
FastAPI 0.155.0, PostgreSQL 16.15 (local instance), 4 CPU cores. Date: 2026-10-20.

## Baseline of the existing repository

The repository contained only a Streamlit chatbot template (`streamlit_app.py`),
with no tests, build or type-check configuration. Recorded check:
`python -m py_compile streamlit_app.py` → OK. No SECURITY CENTER code was present
(see ARCHITECTURE.md → Origin), so no existing scheduling/payroll tests could be run.

## Automated suite — 66 tests

| File | Tests | Scope |
|---|---|---|
| `tests/test_engine.py` | 22 | demand expansion, overnight, DST spring/autumn, month/year boundary, holiday modes, multiple positions; solver invariants (every result re-validated by the independent rules validator): insufficient staff, no qualified staff, expired qualification, rest/overlap, absence & weekly unavailability, site eligibility/prohibition, weekly limit, locked & conflicting lock, fixed work outside scope, consecutive days, night fairness, stability, greedy hint compliance, proven full coverage, symmetry breaking with locked position |
| `tests/test_payroll_engine.py` | 14 | hand-computed examples: regular week & employer cost, overwork/overtime, Sunday night crossing weeks, public holiday, month boundary split, DST fall-back night, part-time extra hours, contract below statutory, monthly salary proration & premium, HALF_UP rounding, weekly categories across months, site cost allocation sum, shift cost estimate, no terms |
| `tests/test_workflow_services.py` | 6 | full service workflow on a real DB (generate → validate → coverage → publish → leave after publication → replacement → publish → attendance → reconcile → payroll run → lock → blocked edits → reopen → v2 run → audit), regenerate subset with locks, manual edit validation + optimistic lock, leave after publication blocks locked regeneration, published immutability & attendance/plan match, append-only corrections |
| `tests/test_web.py` | 24 | login/lockout, every screen renders, 12 role/permission cases, compensation hidden & blocked, reviewer POST blocked, CSRF, company isolation (reads, writes, listings), foreign ids in exports/versions, end-to-end HTTP workflow across three roles, CSV import all-or-nothing, cookie & security headers, session revocation on deactivation |

Commands and results:

```
$ pytest
66 passed, 1 warning in 105.19s
$ INDIGO_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/indigo_test pytest
66 passed, 1 warning in 116.20s
```

The single warning is a Starlette deprecation notice about `httpx` in the test client.

Payroll expected values were computed by hand from the stated rule parameters
(shown in comments in the tests). They verify the arithmetic of the configured
rules, **not** that the rules match Greek law.

## Scripted pilot (synthetic data)

`python -m indigo.cli pilot 2026 11` on a fresh SQLite database:

```
1. Εισαγωγή: 20 εργαζόμενοι, 3 σημεία, 5 απαιτήσεις κάλυψης
2-3. Αυτόματο πρόγραμμα: complete, κάλυψη 100.0% (0.0 ακάλυπτες ώρες από 2688.0), κάλυψη=OPTIMAL, ήπιοι στόχοι=FEASIBLE, 30.04 δευτ.
4. Έλεγχος: 0 παραβιάσεις σκληρών κανόνων· δημοσιεύθηκε v1 (321 βάρδιες)
5. Απουσία 11/11–12/11: 2 επηρεαζόμενες βάρδιες, 2 αντικαταστάσεις, 0 χωρίς αντικαταστάτη· 2 αλλαγές δημοσιεύθηκαν ως v2
6. Παρουσίες: 102 καταχωρίσεις, 5 βάρδιες χωρίς καταγραφή (προσομοίωση)
7. Συμφωνία: 107 γραμμές, 30 αποκλίσεις· εγκρίθηκαν 102 από παρουσία και 2 ρητά βάσει προγράμματος
8. Μισθοδοσία (ΕΚΤΙΜΗΣΗ, μη επαληθευμένοι κανόνες) v1: μικτά 11712.01 €, κόστος εργοδότη 14264.06 € για 868.8 εγκεκριμένες ώρες
9. Εξαγωγές: costs_estimate.csv, coverage_gaps.csv, hours.csv, payroll_estimate.csv, schedule.csv
```

Before the solver improvements (greedy hint, symmetry breaking, zero-bound
recognition) the same pilot ended with 8 uncovered hours, `coverage=FEASIBLE`,
and the report correctly attributed the gap to the time limit
("NOT_ASSIGNED"), not to staff shortage.

Also verified manually: `python -m indigo.cli backup …` produced a consistent
SQLite copy (642 assignment rows); the app served all screens in Chromium
(Playwright screenshots of the generation, result, board and dashboard pages).

## Solver timing (demo data, 4 workers)

| Data | Slots | Coverage phase | Result |
|---|---|---|---|
| 24 employees, 4 sites, Nov 2026 | 286 | ≈15 s (before fix) | 100 %, proven |
| 20 employees, 3 sites, Nov 2026 (pilot CSV) | 321 | ≈4 s with greedy hint (was >12 s) | 100 %, proven |

Soft-objective phase is time-limited and reported as `FEASIBLE` (not proven optimal).

## Not verified

* Docker image build (Docker Hub 429 on the base image) — see DEPLOYMENT.md.
* Behaviour with real customer data and real statutory rules.
* Load/performance beyond ~25 employees per month; multi-user concurrency beyond the revision checks.
* Browser compatibility beyond Chromium.

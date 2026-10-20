# Architecture

## Origin

The task was to extract this product from an existing *SECURITY CENTER*
application. **That application's source was not available in this
environment**: the only repository accessible to the session
(`skliasnikolaos-ui/chatbot`) contains a Streamlit chatbot template (≈60
lines, OpenAI chat demo) with no scheduling, payroll, database, auth or tests.
Nothing could therefore be reused or verified from SECURITY CENTER; INDIGO
WORKFORCE was implemented from scratch as a self-contained application in
`indigo-workforce/`, leaving the original repository files untouched. See
HANDOFF.md for how to compare it with SECURITY CENTER once access is granted.

## Layers

```
indigo/
  config.py           environment-only settings (no secrets in code)
  timeutil.py         Europe/Athens ↔ UTC, night/Sunday/holiday splitting, DST policy
  holidays.py         default Greek holiday calendar (Orthodox Easter computation)
  engine/             PURE scheduling core (no DB)
    types.py          RequirementSpec, Slot, EmployeeSpec, FixedWork, Weights, Problem
    demand.py         requirements → concrete slots (demand independent of assignments)
    rules.py          authoritative hard-constraint validator + blocker explanations
    solver.py         CP-SAT model (two lexicographic phases) + greedy hint + gap analysis
  payroll_engine.py   PURE Decimal payroll/cost estimate engine
  db.py, models.py    SQLAlchemy 2 models; UTCDateTime and exact Money types
  migrations.py       forward-only additive migrations, recorded in schema_migrations
  security.py         Argon2, server-side sessions, CSRF tokens, lockout
  authz.py            role → permission matrix (enforced per route)
  services/           business workflows over the DB (scheduling, replacements,
                      attendance, payroll, reports, importer, demo, companies, audit, scope)
  web/                FastAPI + Jinja2 server-rendered Greek UI (no JS framework)
  cli.py, pilot.py    migrate / create-company / seed-demo / backup / pilot
```

The engine and payroll modules are pure functions over dataclasses, which is
what the invariant and payroll tests exercise directly. Services adapt the
database to engine types and own transactions, versioning and audit.

## Data model (main tables)

* `companies`, `users` (role per user, one company per user), `user_sessions`, `audit_log`.
* Employees: `employees`, `employee_qualifications` (validity dates), `weekly_unavailability`,
  `absences` (status requested/approved/…), `employee_site_rules` (eligible/prohibited/preferred),
  `fixed_posts`, `compensation_terms` (effective-dated, contractual only).
* Demand: `clients`, `contracts` (active period), `sites`, `coverage_requirements`, `holidays`.
* Schedules: `schedule_periods` (company × month, `revision`), `schedule_versions`
  (draft/published/superseded/discarded, `revision`), `assignments` (per version; slot key, UTC times, locked, source),
  `generation_runs` (parameters + full solver report).
* Time: `attendance_records` (actual; append-only, `supersedes_id`), `approved_hours`
  (payable; append-only, basis attendance/plan/manual, `supersedes_id`).
* Payroll: `payroll_rule_sets` (statutory parameters, effective-dated, `verified` flag + sources),
  `payroll_periods` (open/locked, `reopen_count`, `revision`), `payroll_runs` (versioned, rule snapshot),
  `payroll_lines`, `payroll_employee_totals`.

Every business row has `company_id`. Reads by id use `services.scope.get_owned`,
which returns 404 for another company's rows; listings filter by the caller's
company. Users cannot choose a company — it comes from their session.

## Time model

* Instants are stored as UTC; local wall times are Europe/Athens (zoneinfo).
* A requirement window ending at or before its start continues to the next day
  (equal start/end = 24h). Windows are split into shifts at **wall-clock**
  boundaries (shift changes happen at 07:00 regardless of DST); durations are
  real elapsed time (a 22:00–06:00 shift is 7h on the spring-forward night and
  9h on the fall-back night).
* Non-existent local times (spring gap) resolve forward; ambiguous ones (autumn) to the first occurrence.
* A shift belongs to the *service date* on which it starts (weekly/monthly
  limits, consecutive days). Payroll splits minutes by actual local date (a
  31 Dec 20:00–1 Jan 08:00 shift pays 4h in December and 8h as New Year's Day).

## Scheduling model

Slot = requirement × service date × shift segment × position. Variables
`x[e,s]` exist only for pairs that pass static eligibility (employment dates,
approved absences, weekly unavailability, qualification present and valid on
every touched date, site prohibition/explicit eligibility, maximum shift
length, conflicts with fixed work outside the optimisation scope). Each slot
has an "uncovered" literal `u[s]`, with `Σ x + u = 1`.

Hard constraints (never relaxed):
* no overlap and minimum rest — `AtMostOne` over the maximal cliques of the
  interval graph of `[start, end + rest)`; exact for interval graphs;
* weekly (ISO week) and monthly hour limits, including fixed out-of-scope work;
* maximum consecutive working days (sliding windows, including adjacent-month published work);
* locked assignments fixed to 1 — if a lock itself breaks a rule, generation
  stops with `locked_conflict` and lists the violations; locks are never dropped.

Objective, lexicographic:
1. **Coverage**: minimise uncovered minutes. A greedy construction (best of
   three orderings, hard-rule compliant) seeds the search as a hint. Symmetry
   breaking fills identical positions of a shift in order (skipped where a
   position is locked). The result is `OPTIMAL` (proven maximum coverage —
   including the trivial case of zero uncovered minutes) or `FEASIBLE` (time
   limit; coverage may be improvable). The UI states which.
2. **Soft objectives**, with coverage constrained to the phase-1 value, weighted
   0–10 per objective; each weight multiplies a published unit (≈ points per €1):
   cost, night and weekend fairness (max − min), preferences, continuity per
   post, balance against contract hours, stability against the published
   schedule, fixed posts, preferring regular over relief staff. Status
   `OPTIMAL`/`FEASIBLE` is reported; the product never claims optimality that was not proven.

The independent validator `engine/rules.py` re-checks every produced or
edited schedule. Publishing is blocked while any hard violation exists;
publishing with uncovered slots requires explicit acknowledgement.

Gap explanation: for each uncovered slot, every employee is tested with
`candidate_blockers` against the final schedule and classified as
not qualified (qualification/site/length), unavailable (employment/absence/
weekly unavailability) or capacity-blocked (overlap/rest/limits/consecutive
days). The slot cause is `NO_QUALIFIED`, `UNAVAILABLE`, `CAPACITY`, or
`NOT_ASSIGNED` (someone was free — only possible after a time-limited
coverage phase), each with a Greek remedy hint.

## Versions, publication, concurrency

* A period has at most one open draft. Generation creates a new draft from the
  current draft or the published version, carrying assignments outside the
  regenerated scope and keeping locked ones; the old draft is marked `discarded`.
* Publishing marks the previous published version `superseded`; versions are
  never edited after publication, so assignment history is preserved.
* All transitions and edits use compare-and-set on `revision`
  (`UPDATE … WHERE id=? AND revision=?`). Stale forms get a conflict message.

## Replacements

An approved absence → affected assignments (published/draft, by touched local
dates) → candidates evaluated with the same validator against the period's
draft (or published plan), ranked by a transparent score (estimated shift cost,
hours above weekly contract, site familiarity in the last 60 days, preference
conflicts, relief), showing week/month hours after, rest before/after and cost.
Applying stages the change in the draft; the manager publishes it.

## Attendance and payroll

Planned (published assignments), actual (`attendance_records`) and approved
(`approved_hours`) are separate. Corrections supersede rows with a mandatory
reason. "Approve as planned" is an explicit action recorded as `basis=plan`
and flagged in reconciliation. Payroll periods, when locked, block attendance,
approvals and recalculation for dates in that month; reopening needs the admin
role and a reason, and is audited. Each calculation is a new numbered run with
a snapshot of the rule set; earlier runs are kept.

Payroll engine: see the module docstring in `indigo/payroll_engine.py`.
Contractual premiums can only increase the statutory minimum (max applied,
warning when a contract is below). All amounts are `Decimal`, rounded per line
with ROUND_HALF_UP.

## Security

Argon2id passwords (≥10 chars), lockout after 5 failures (15 min), server-side
sessions stored as HMAC digests (revocable; revoked on role/password/active
changes), HttpOnly + SameSite=Strict cookie (Secure in production), CSRF token
per session on every POST, CSP/X-Frame-Options/no-store headers, permission
check in every route (including CSV exports), company scoping in every query,
input validation with bounds, PII-scrubbing log filter and opaque error ids,
production refuses to start with the development secret or without HTTPS cookies.

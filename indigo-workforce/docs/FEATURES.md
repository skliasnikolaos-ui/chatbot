# Feature inventory (actual status)

Legend: ✅ implemented and covered by automated tests · 🟡 implemented, partially tested or simplified ·
⚠️ estimate / needs external validation · ❌ not implemented

| # | Feature | Status | Evidence / notes |
|---|---|---|---|
| 1 | Company accounts, strict data isolation | ✅ | `test_web.py::test_company_isolation`, `test_foreign_ids_in_exports_and_versions` |
| 2 | Roles admin / scheduler / payroll / reviewer, server-side | ✅ | `authz.py`; `test_role_permissions_enforced_server_side`, `test_reviewer_cannot_post`, compensation hidden & blocked |
| 3 | Greek UI | ✅ | all screens; render test for every screen |
| 4 | Employee details, employment dates, contract type/hours | ✅ | |
| 5 | Availability (weekly) and unavailable periods / leave / sickness | ✅ | approved absences are hard constraints; requested ones are not |
| 6 | Qualifications/licences with validity, expiry respected | ✅ | `test_expired_qualification_is_excluded`, workflow test (D004) |
| 7 | Eligible / prohibited / preferred sites | ✅ | `test_site_eligibility_and_prohibition` |
| 8 | Shift preferences (nights, weekends) | ✅ | soft objective |
| 9 | Compensation terms with effective dates | ✅ | previous term auto-closed (audited) |
| 10 | Working-time limits (week/month/shift length), rest, consecutive days | ✅ | invariant tests |
| 11 | Fixed posts, relief staff | 🟡 | soft objectives; no dedicated test of fixed-post adherence |
| 12 | Clients, contracts (active period), sites, site restrictions (text) | ✅ | demand limited to contract period |
| 13 | Coverage requirements: weekdays, windows, overnight, 24h split into shifts, headcount, qualifications, holiday rule, validity, billing rate | ✅ | `test_engine.py` demand tests |
| 14 | Overnight, month/year boundaries, holidays, DST, multiple positions | ✅ | dedicated tests (engine and payroll) |
| 15 | Partial coverage with explanations, missing hours, remedies | ✅ | `test_insufficient_staff…`, `test_no_qualified_staff_cause` |
| 16 | CP-SAT solver, lexicographic coverage → soft objectives, visible priorities | ✅ | |
| 17 | Proven vs time-limited result distinction | ✅ | `coverage_status` / `optimisation_status` shown |
| 18 | Monthly generation, regenerate selected dates/sites, keep locks | ✅ | `test_regenerate_subset_preserves_locked_and_outside_scope` |
| 19 | Locked assignment that breaks a rule → explicit stop | ✅ | `test_leave_after_publication_blocks_locked_regeneration` |
| 20 | Preview/diff, approval & publication, versioning | ✅ | |
| 21 | Manual editing with validation | ✅ | `test_manual_edit_validation_and_optimistic_lock` |
| 22 | Concurrent change protection | ✅ | compare-and-set revisions; stale form test (service + HTTP) |
| 23 | Absence after publication → affected shifts → ranked candidates → approval | ✅ | service and HTTP end-to-end tests |
| 24 | Planned / actual / approved hours separation, reconciliation | ✅ | |
| 25 | Append-only corrections with reasons, history view | ✅ | `test_corrections_are_append_only` |
| 26 | Payroll: regular, night, Sunday/holiday, overtime categories, employer cost | ⚠️ | hand-checked examples in `test_payroll_engine.py`; **rules not legally verified** |
| 27 | Contract vs statutory separation, no undercutting | ✅ | `test_contract_cannot_undercut_statutory_premium` |
| 28 | Decimal money, explicit rounding | ✅ | `test_half_up_rounding` |
| 29 | Versioned payroll runs, period lock, audited reopen | ✅ | workflow test |
| 30 | Cost per employee / site / shift / month, planned vs actual, margin | 🟡 | implemented; tested for consistency (allocation sums), not against real data |
| 31 | Accountant review export | ✅ | CSV with estimate disclaimer |
| 32 | Contributions/deductions | ⚠️ | flat employer/employee % only; no ceilings, no income tax, no net pay |
| 33 | Reports & CSV exports (schedule, gaps, hours, costs, payroll) | ✅ | |
| 34 | CSV import employees / sites & requirements, all-or-nothing | ✅ | `test_import_employees_dry_run_then_commit`, pilot |
| 35 | Audit log of sensitive changes | ✅ | viewer for admin/reviewer |
| 36 | Synthetic demo data, labelled and isolated | ✅ | `is_demo` company + banner |
| 37 | Backup/restore | 🟡 | SQLite online backup command tested; PostgreSQL via pg_dump (documented, not automated) |
| 38 | Error monitoring without PII | 🟡 | scrubbed logs + error ids; no external monitoring service integrated |
| 39 | PostgreSQL support | ✅ | full suite passes on PostgreSQL 16 |
| 40 | ERGANI / digital work card, official payroll filing | ❌ | not implemented; never claimed |
| 41 | Declared schedules (to authorities) | ❌ | schedule CSV export only |
| 42 | LLM explanations | ❌ | not needed; all constraint handling is deterministic |
| 43 | Partial-day absences (one-off hours) | ❌ | full-day absences + weekly windows only |
| 44 | Employee self-service / mobile | ❌ | out of first-release scope |

# Implementation roadmap

## Done (this iteration)
Vertical workflow employees → site demand → automatic schedule → coverage
report, and the remaining first-release scope listed in FEATURES.md, with
tests on SQLite and PostgreSQL.

## Next — before the customer pilot with real data
1. Owner/accountant: verify payroll parameters and the holiday list against authoritative sources;
   enter a verified rule set with sources (release blocker 1).
2. Confirm the customer's actual rules in a workshop (rest, max hours, shift
   patterns, overtime practice, collective agreement) and adjust defaults.
3. Provision the pilot environment (PostgreSQL, TLS, backups, restore test, log shipping).
4. Run the hands-on pilot (docs/PILOT.md) with the scheduling manager.
5. Decide the product licence and name.

## Then — first production release
* Background job queue for generation (progress, cancellation), larger companies.
* Partial-day absences; weekly-rest rule; cross-month fairness memory.
* Daily overtime, overtime caps, compounding premiums, contribution ceilings — only with verified rules.
* Accountant export in the format of the customer's payroll software.
* XLSX/PDF exports of the schedule for clients and staff.
* Per-IP rate limiting, optional 2FA for admins, session management screen.
* Automated PostgreSQL backups and monitoring alerts.

## Later
* ERGANI digital work card integration (only once specified, implemented and tested).
* Employee self-service (availability, swaps), notifications.
* LLM-assisted explanations of solver results (never as the constraint engine).

# Customer pilot procedure

Use synthetic data until real customer data is explicitly provided for an
agreed environment (with a data-processing agreement).

## A. Automated rehearsal (synthetic)

```bash
python -m indigo.cli pilot 2026 11
```

Runs every pilot step on `sample_data/*.csv` in a new demo-flagged company and
writes exports to `var/pilot_<id>/`. Recorded output: see TEST_RESULTS.md.

## B. Hands-on pilot with the customer's scheduling manager

1. **Setup** (admin): Ρυθμίσεις → users for each role; check holidays for the
   year; enter qualification codes; review payroll parameters with the accountant
   (they stay "ΜΗ επαληθευμένες" until verified with sources).
2. **Import employees and sites**: Εργαζόμενοι → Εισαγωγή CSV; Πελάτες & σημεία →
   Εισαγωγή (run "Έλεγχος" first; the import is all-or-nothing).
3. **Coverage requirements**: open each site, check the 7-day shift preview;
   set site eligibility per employee where required.
4. **Generate the month**: Πρόγραμμα → Αυτόματη δημιουργία; review the
   staffing-capacity table first; run; read the result (coverage proven or
   time-limited, gaps with causes, per-employee distribution).
5. **Review & approve**: board view by site and by employee; edit/lock cells
   if needed; Αλλαγές / δημοσίευση → publish (acknowledge gaps explicitly).
6. **Absence & replacement**: Απουσίες → register a sickness on published
   dates → choose a candidate → review the draft diff → publish.
7. **Attendance**: Παρουσίες → enter actual times; correct one with a reason;
   payroll operator approves (from attendance, or explicitly "as planned").
8. **Reconcile & cost**: check the flags; Μισθοδοσία → calculate (estimate),
   review warnings; Αναφορές → planned vs actual cost, margin.
9. **Export**: schedule, gaps, hours, costs, accountant payroll CSV.
10. **Lock** the payroll period; demonstrate a reopen with a reason (admin) and the audit log.

## Pilot exit criteria (suggested)

* Manager confirms the generated schedule respects their real rules
  (record every rule that is missing or wrong).
* Accountant reconciles the payroll estimate for ≥3 employees against their
  own calculation and signs off parameters (or lists required changes).
* No cross-company leakage, no unhandled errors in logs during the pilot.

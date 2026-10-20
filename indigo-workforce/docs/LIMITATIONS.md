# Known limitations and release blockers

## Release blockers (must be resolved before production use with real data)

1. **Payroll rules are not legally verified.** The default parameters
   (night 25 %, Sunday/holiday 75 %, overwork 20 % between 40–45 h, overtime 40 %
   above 45 h, part-time extra 12 %, employer 21.79 %, employee 13.37 %,
   monthly→hourly divisor 166.6667) are placeholders entered from general
   knowledge and **have not been checked against authoritative sources** (Greek labour law,
   applicable collective agreement for security staff, EFKA rates for the
   relevant year). Each rule set carries a `verified` flag; all screens and
   exports label results as estimates. An accountant must review and enter
   verified, effective-dated parameters (with sources) before any figure is
   used for pay.
2. **Payroll model simplifications** (even with verified parameters):
   overtime is weekly only (no daily overtime > 8h, no annual overtime caps, no
   legal-limit "illegal overtime" category); premiums are additive on the base
   rate (no compounding of night + overtime); contribution ceilings, special
   insurance categories (e.g. heavy/unhealthy occupations), income tax,
   Christmas/Easter bonuses and leave allowance (only an optional flat accrual
   %), leave pay and sick pay are not computed; monthly salary proration is
   calendar-day based. There is no net pay.
3. **No ERGANI / digital work card integration** and no official filing of
   schedules or payroll. Do not represent the product as compliant.
4. **SECURITY CENTER source was not available**, so no reuse or migration of
   the customer's existing data/logic was possible. Requirements were taken from
   the brief only; the customer's real workflows must be confirmed in the pilot.
5. **No real-data operational environment yet**: hosting, TLS, backups,
   monitoring and data-processing agreements (GDPR) for the customer must be set up.

## Functional limitations

* Absences are whole days; partial-day one-off unavailability is not modelled
  (weekly recurring windows are).
* Requested (not approved) absences do not block scheduling.
* Weekly/monthly limits and consecutive days attribute a shift to the date it starts.
* Minimum rest is per employee (default 11 h); no weekly-rest (24 h) rule is enforced
  separately beyond the consecutive-days limit.
* Fairness is measured within the optimised scope (one month), not across months.
* Soft objectives are usually `FEASIBLE`, not proven optimal, within the
  time limit for realistic months; the UI says so. On the 24-employee demo,
  night-shift distribution was observed to be uneven (0–11 per person) when
  the time limit cut the optimisation short — raise the time limit or the
  fairness weight if it matters.
* Generation runs synchronously inside the web request (bounded by the time limit, max 300 s).
  For many concurrent users or very large companies a job queue is needed.
* Changing a coverage requirement does not rewrite existing schedules; affected
  assignments show up as `UNKNOWN_SLOT` violations to resolve.
* Holiday calendar is a default seed; which days carry premiums must be confirmed.
* Planned cost uses a per-shift estimate without weekly overtime; actual cost
  uses the full engine on approved hours.
* No e-mail/SMS notifications, no employee self-service, no mobile app.
* Login throttling is per account (lockout), not per IP; put rate limiting at the reverse proxy.
* CSV is the only export format (opens in Excel; UTF-8 BOM, `;`).
* Single company per user account.

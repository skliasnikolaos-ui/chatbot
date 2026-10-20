"""Payroll periods, versioned runs, locking and controlled reopening."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import payroll_engine as pe
from ..db import utcnow
from ..models import ApprovedHours, Employee, PayrollEmployeeTotal, PayrollLine, PayrollPeriod, PayrollRun
from . import audit, scheduling as sch
from .attendance import LockedPeriod


def get_period(db: Session, company_id: int, year: int, month: int) -> PayrollPeriod:
    p = db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id == company_id, PayrollPeriod.year == year,
                                              PayrollPeriod.month == month))
    if p is None:
        p = PayrollPeriod(company_id=company_id, year=year, month=month)
        db.add(p)
        db.flush()
    return p


def current_run(db: Session, period: PayrollPeriod) -> PayrollRun | None:
    return db.scalar(select(PayrollRun).where(PayrollRun.period_id == period.id, PayrollRun.is_current.is_(True)))


def compute(db: Session, company_id: int, year: int, month: int, source: str = "approved") -> list[pe.EmployeePayroll]:
    """Compute per-employee payroll estimates. source='approved' (actual) or 'planned' (published schedule)."""
    start, end = sch.month_range(year, month)
    w0, w1 = pe.week_window(year, month)
    hol = sch.holidays_set(db, company_id, w0, w1)
    _, rules = sch.active_ruleset(db, company_id, start)
    intervals: dict[int, list[pe.WorkInterval]] = defaultdict(list)
    if source == "approved":
        for ah in db.scalars(select(ApprovedHours).where(ApprovedHours.company_id == company_id, ApprovedHours.status == "active",
                                                         ApprovedHours.work_date >= w0 - timedelta(days=1),
                                                         ApprovedHours.work_date <= w1)):
            intervals[ah.employee_id].append(pe.WorkInterval(ah.start_utc, ah.end_utc, ah.site_id, f"ah:{ah.id}"))
    else:
        for a in sch.published_assignments_between(db, company_id, w0 - timedelta(days=1), w1):
            intervals[a.employee_id].append(pe.WorkInterval(a.start_utc, a.end_utc, a.site_id, f"as:{a.id}"))
    emps = list(db.scalars(select(Employee).where(Employee.company_id == company_id)))
    out = []
    for e in emps:
        ivs = intervals.get(e.id, [])
        comp = sch.compensation_for(db, company_id, e.id, end) or sch.compensation_for(db, company_id, e.id, start)
        employed = (not e.hire_date or e.hire_date <= end) and (not e.termination_date or e.termination_date >= start)
        if not ivs and not (comp and comp.pay_basis == "monthly" and employed):
            continue
        r = pe.calculate(e.id, year, month, comp, rules, ivs, hol, employed_from=e.hire_date, employed_to=e.termination_date)
        if r.minutes.get("total") or r.gross or r.warnings:
            out.append(r)
    return out


def run(db: Session, company_id: int, user_id: int, year: int, month: int, expected_revision: int | None) -> PayrollRun:
    period = get_period(db, company_id, year, month)
    if period.status == "locked":
        raise LockedPeriod("Η περίοδος είναι κλειδωμένη· απαιτείται επανάνοιγμα.")
    _bump(db, period, expected_revision)
    rs, rules = sch.active_ruleset(db, company_id, sch.month_range(year, month)[0])
    results = compute(db, company_id, year, month, "approved")
    db.execute(update(PayrollRun).where(PayrollRun.period_id == period.id).values(is_current=False))
    num = (db.scalar(select(func.max(PayrollRun.version_no)).where(PayrollRun.period_id == period.id)) or 0) + 1
    totals = {"gross": Decimal(0), "employer_contributions": Decimal(0), "employee_contributions": Decimal(0),
              "bonus_accrual": Decimal(0), "employer_cost": Decimal(0), "paid_minutes": 0, "employees": len(results)}
    warnings = []
    pr = PayrollRun(company_id=company_id, period_id=period.id, version_no=num, is_current=True, ruleset_id=rs.id if rs else None,
                    ruleset_snapshot={"rules": rules, "verified": bool(rs and rs.verified), "name": rs.name if rs else "Προεπιλογές (μη επαληθευμένες)"},
                    created_by=user_id)
    db.add(pr)
    db.flush()
    names = {e.id: e.full_name for e in db.scalars(select(Employee).where(Employee.company_id == company_id))}
    for r in results:
        for ln in r.lines:
            db.add(PayrollLine(company_id=company_id, run_id=pr.id, employee_id=r.employee_id, site_id=ln.site_id,
                               category=ln.category, minutes=ln.minutes, rate=ln.rate, pct=ln.pct, amount=ln.amount))
        db.add(PayrollEmployeeTotal(company_id=company_id, run_id=pr.id, employee_id=r.employee_id,
                                    paid_minutes=r.minutes.get("total", 0), gross=r.gross,
                                    employer_contributions=r.employer_contributions, employee_contributions=r.employee_contributions,
                                    bonus_accrual=r.bonus_accrual, employer_cost=r.employer_cost,
                                    site_costs={str(k): str(v) for k, v in r.site_costs().items()}, warnings=r.warnings))
        for k in ("gross", "employer_contributions", "employee_contributions", "bonus_accrual", "employer_cost"):
            totals[k] += getattr(r, k)
        totals["paid_minutes"] += r.minutes.get("total", 0)
        warnings += [f"{names.get(r.employee_id, r.employee_id)}: {w}" for w in r.warnings]
    pr.totals = {k: str(v) for k, v in totals.items()}
    pr.warnings = warnings
    audit.log(db, company_id, user_id, "payroll.run", "payroll_run", pr.id, period=f"{year}-{month:02d}", version=num)
    db.commit()
    return pr


def _bump(db: Session, period: PayrollPeriod, expected: int | None) -> None:
    q = update(PayrollPeriod).where(PayrollPeriod.id == period.id)
    if expected is not None:
        q = q.where(PayrollPeriod.revision == int(expected))
    if db.execute(q.values(revision=PayrollPeriod.revision + 1)).rowcount != 1:
        db.rollback()
        raise sch.ConflictError("Η περίοδος άλλαξε από άλλον χρήστη. Ανανεώστε τη σελίδα.")
    db.refresh(period)


def lock(db: Session, company_id: int, user_id: int, year: int, month: int, expected_revision: int | None) -> None:
    period = get_period(db, company_id, year, month)
    if period.status == "locked":
        return
    if current_run(db, period) is None:
        raise ValueError("Υπολογίστε πρώτα τη μισθοδοσία της περιόδου.")
    _bump(db, period, expected_revision)
    period.status, period.locked_at, period.locked_by = "locked", utcnow(), user_id
    audit.log(db, company_id, user_id, "payroll.lock", "payroll_period", period.id, period=f"{year}-{month:02d}")
    db.commit()


def reopen(db: Session, company_id: int, user_id: int, year: int, month: int, reason: str, expected_revision: int | None) -> None:
    if not reason or len(reason.strip()) < 5:
        raise ValueError("Απαιτείται αναλυτική αιτιολογία επανανοίγματος.")
    period = get_period(db, company_id, year, month)
    if period.status != "locked":
        raise ValueError("Η περίοδος δεν είναι κλειδωμένη.")
    _bump(db, period, expected_revision)
    period.status = "open"
    period.reopen_count += 1
    audit.log(db, company_id, user_id, "payroll.reopen", "payroll_period", period.id, period=f"{year}-{month:02d}", reason=reason.strip())
    db.commit()

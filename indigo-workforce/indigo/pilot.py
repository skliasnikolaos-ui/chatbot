"""Repeatable pilot workflow on SYNTHETIC data (sample_data/*.csv).

  python -m indigo.cli pilot 2026 11

Creates a new demo-flagged company in the configured database and runs:
import → requirements → monthly generation → review/publish → absence &
replacement → attendance → reconciliation & approval → payroll estimate →
exports (written to var/pilot_<company>/).
"""
from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from . import db as dbm
from .models import Absence, Employee, EmployeeSiteRule, Site
from .services import attendance as att, importer, payroll as pay, replacements as rep, reports, scheduling as sch
from .services.companies import create_company

ROOT = Path(__file__).resolve().parent.parent


def run_pilot(year: int, month: int, out_root: str | Path | None = None, time_limit_s: float = 30.0) -> str:
    lines: list[str] = []
    say = lines.append
    with dbm.get_session() as db:
        c = create_company(db, "PILOT – Συνθετικά δεδομένα", is_demo=True, years=[year - 1, year, year + 1])
        db.commit()
        cid = c.id
        out = Path(out_root or ROOT / "var") / f"pilot_{cid}"
        out.mkdir(parents=True, exist_ok=True)
        say(f"# Πιλοτική ροή – εταιρεία #{cid} (ΣΥΝΘΕΤΙΚΑ) – {month:02d}/{year}")

        # 1. import employees & sites/requirements
        r1 = importer.import_employees(db, cid, None, (ROOT / "sample_data/employees.csv").read_text(encoding="utf-8"), commit=True)
        r2 = importer.import_sites(db, cid, None, (ROOT / "sample_data/sites.csv").read_text(encoding="utf-8"), commit=True)
        assert not r1["errors"] and not r2["errors"], (r1, r2)
        say(f"1. Εισαγωγή: {r1['created']} εργαζόμενοι, {r2['sites']} σημεία, {r2['requirements']} απαιτήσεις κάλυψης")
        # sites requiring explicit eligibility: approve every second employee
        for s_ in db.scalars(select(Site).where(Site.company_id == cid, Site.requires_explicit_eligibility.is_(True))):
            for k, e in enumerate(db.scalars(select(Employee).where(Employee.company_id == cid).order_by(Employee.code))):
                if k % 2 == 0 or k >= 18:
                    db.add(EmployeeSiteRule(company_id=cid, employee_id=e.id, site_id=s_.id, rule="eligible"))
        db.commit()

        # 2-3. generate
        period = sch.get_period(db, cid, year, month)
        db.commit()
        run = sch.generate(db, cid, None, year, month, time_limit_s=time_limit_s, expected_period_revision=period.revision)
        st = run.report["stats"]
        say(f"2-3. Αυτόματο πρόγραμμα: {run.status}, κάλυψη {st.get('coverage_pct')}% "
            f"({st.get('uncovered_hours')} ακάλυπτες ώρες από {st.get('demand_hours')}), κάλυψη={run.report['coverage_status']}, "
            f"ήπιοι στόχοι={run.report['optimisation_status']}, {st.get('seconds')} δευτ.")
        for rec in run.report.get("recommendations", []):
            say(f"    - {rec['label']}: {rec['hours']} ώρες → {rec['hint']}")
        v = db.get(sch.ScheduleVersion, run.version_id)

        # 4. review & publish
        check = sch.validate_version(db, v)
        assert not check["violations"], [x.as_dict() for x in check["violations"]]
        sch.publish(db, cid, None, v.id, v.revision, accept_gaps=True)
        say(f"4. Έλεγχος: 0 παραβιάσεις σκληρών κανόνων· δημοσιεύθηκε v{v.number} ({len(check['assignments'])} βάρδιες)")

        # 5. absence after publication + replacement
        assigns = sch.assignments_of(db, v.id)
        target_day = assigns[len(assigns) // 3].service_date
        victim = assigns[len(assigns) // 3].employee_id
        ab = Absence(company_id=cid, employee_id=victim, kind="sick", start_date=target_day, end_date=target_day + timedelta(days=1),
                     status="approved", notes="Πιλοτική προσομοίωση")
        db.add(ab)
        db.commit()
        affected = rep.affected_assignments(db, cid, ab)
        replaced = uncovered = 0
        for a in affected:
            if a.version_id != v.id:
                continue
            cands = rep.candidates(db, cid, a.id)
            pick = cands["eligible"][0]["employee_id"] if cands["eligible"] else None
            rep.apply(db, cid, None, a.id, pick, "Πιλοτική αντικατάσταση")
            replaced += pick is not None
            uncovered += pick is None
        draft = sch.version_with_status(db, sch.get_period(db, cid, year, month).id, "draft")
        if draft:
            diff = sch.diff_versions(db, draft, v)
            db.refresh(draft)
            sch.publish(db, cid, None, draft.id, draft.revision, accept_gaps=True)
            say(f"5. Απουσία {target_day:%d/%m}–{target_day + timedelta(days=1):%d/%m}: {len(affected)} επηρεαζόμενες βάρδιες, "
                f"{replaced} αντικαταστάσεις, {uncovered} χωρίς αντικαταστάτη· {len(diff)} αλλαγές δημοσιεύθηκαν ως v{draft.number}")
            v = draft
        else:
            say("5. Απουσία χωρίς επηρεαζόμενες βάρδιες")

        # 6. attendance for the first 10 days (synthetic deviations)
        rnd = random.Random(11)
        first = sch.month_range(year, month)[0]
        planned = [a for a in sch.assignments_of(db, v.id) if a.service_date < first + timedelta(days=10)]
        n_rec = n_missing = 0
        records = {}
        for a in planned:
            roll = rnd.random()
            if roll < 0.05:
                n_missing += 1
                continue
            delta_in = timedelta(minutes=rnd.choice([0, 0, 0, 5, 20])) if roll < 0.5 else timedelta(0)
            delta_out = timedelta(minutes=rnd.choice([0, 0, 30, 60])) if roll > 0.8 else timedelta(0)
            records[a.id] = att.record_attendance(db, cid, None, a.employee_id, a.start_utc + delta_in, a.end_utc + delta_out, assignment_id=a.id)
            n_rec += 1
        say(f"6. Παρουσίες: {n_rec} καταχωρίσεις, {n_missing} βάρδιες χωρίς καταγραφή (προσομοίωση)")

        # 7. reconcile & approve
        rows = att.reconcile(db, cid, first, first + timedelta(days=9))
        disc = sum(1 for r in rows if any(f.startswith(("Διαφορά", "Χωρίς καταγραφή")) for f in r["flags"]))
        n_app = n_plan = 0
        for r in rows:
            if r["att"] is not None and r["app"] is None:
                att.approve(db, cid, None, employee_id=r["att"].employee_id, start=r["att"].clock_in, end=r["att"].clock_out,
                            basis="attendance", attendance_id=r["att"].id)
                n_app += 1
            elif r["plan"] is not None and r["att"] is None and n_plan < 2:
                att.approve(db, cid, None, employee_id=r["plan"].employee_id, start=r["plan"].start_utc, end=r["plan"].end_utc,
                            basis="plan", assignment_id=r["plan"].id, reason="Πιλοτική: επιβεβαίωση υπευθύνου βάρδιας")
                n_plan += 1
        say(f"7. Συμφωνία: {len(rows)} γραμμές, {disc} αποκλίσεις· εγκρίθηκαν {n_app} από παρουσία και {n_plan} ρητά βάσει προγράμματος "
            f"(οι υπόλοιπες παραμένουν σε εκκρεμότητα)")

        # 8. payroll estimate & costs
        pp = pay.get_period(db, cid, year, month)
        db.commit()
        prun = pay.run(db, cid, None, year, month, pp.revision)
        costs = reports.costs(db, cid, year, month)
        say(f"8. Μισθοδοσία (ΕΚΤΙΜΗΣΗ, μη επαληθευμένοι κανόνες) v{prun.version_no}: μικτά {prun.totals['gross']} €, "
            f"κόστος εργοδότη {prun.totals['employer_cost']} € για {int(prun.totals['paid_minutes']) / 60:.1f} εγκεκριμένες ώρες· "
            f"προγραμματισμένο κόστος μήνα {costs['total_planned']} €")

        # 9. exports
        (out / "schedule.csv").write_bytes(reports.export_schedule(db, cid, v))
        (out / "coverage_gaps.csv").write_bytes(reports.export_gaps(db, cid, year, month))
        (out / "hours.csv").write_bytes(reports.export_hours(db, cid, year, month))
        (out / "costs_estimate.csv").write_bytes(reports.export_costs(db, cid, year, month))
        (out / "payroll_estimate.csv").write_bytes(reports.export_payroll(db, cid, prun))
        say(f"9. Εξαγωγές: {', '.join(sorted(p.name for p in out.iterdir()))} στον φάκελο {out}")
    return "\n".join(lines)

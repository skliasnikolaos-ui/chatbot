"""Persistent data model. Every business table carries company_id; all
queries go through company-scoped helpers (see indigo/services/scope.py)."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, Time, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, Money, UTCDateTime, utcnow

ROLES = {
    "admin": "Διαχειριστής εταιρείας",
    "scheduler": "Υπεύθυνος προγραμματισμού",
    "payroll": "Χειριστής μισθοδοσίας",
    "reviewer": "Αναγνώστης (μόνο ανάγνωση)",
}


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    company: Mapped[Company] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), index=True, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Qualification(Base):
    __tablename__ = "qualifications"
    __table_args__ = (UniqueConstraint("company_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("company_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    hire_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    termination_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    contract_type: Mapped[str] = mapped_column(String(20), default="full_time")  # full_time|part_time|fixed_term
    weekly_contract_hours: Mapped[Decimal] = mapped_column(Money, default=Decimal(40))
    max_hours_week: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True, default=Decimal(48))
    max_hours_month: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    max_shift_hours: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True, default=Decimal(12))
    min_rest_hours: Mapped[Decimal] = mapped_column(Money, default=Decimal(11))
    max_consecutive_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=6)
    is_relief: Mapped[bool] = mapped_column(Boolean, default=False)
    night_preference: Mapped[str] = mapped_column(String(10), default="neutral")
    avoid_weekends: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name}"


class EmployeeQualification(Base):
    __tablename__ = "employee_qualifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    qualification_id: Mapped[int] = mapped_column(ForeignKey("qualifications.id"))
    licence_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    qualification: Mapped[Qualification] = relationship()


class WeeklyUnavailability(Base):
    __tablename__ = "weekly_unavailability"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


ABSENCE_KINDS = {"leave": "Κανονική άδεια", "sick": "Ασθένεια", "unavailable": "Μη διαθεσιμότητα", "other": "Άλλη απουσία"}
ABSENCE_STATUS = {"requested": "Αιτήθηκε", "approved": "Εγκρίθηκε", "rejected": "Απορρίφθηκε", "cancelled": "Ακυρώθηκε"}


class Absence(Base):
    __tablename__ = "absences"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="approved")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    employee: Mapped[Employee] = relationship()


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    vat_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    reference: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    contract_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    requires_explicit_eligibility: Mapped[bool] = mapped_column(Boolean, default=False)
    restrictions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    client: Mapped[Client] = relationship()
    contract: Mapped[Optional[Contract]] = relationship()


class EmployeeSiteRule(Base):
    __tablename__ = "employee_site_rules"
    __table_args__ = (UniqueConstraint("employee_id", "site_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    rule: Mapped[str] = mapped_column(String(20))  # eligible | prohibited | preferred
    site: Mapped[Site] = relationship()


HOLIDAY_MODES = {"normal": "Κανονικά (βάσει ημέρας εβδομάδας)", "skip": "Όχι σε αργίες", "sunday_rule": "Όπως Κυριακή"}


class CoverageRequirement(Base):
    __tablename__ = "coverage_requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    weekdays: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5,6")
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    shift_hours: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    headcount: Mapped[int] = mapped_column(Integer, default=1)
    holiday_mode: Mapped[str] = mapped_column(String(20), default="normal")
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    billing_rate_hourly: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    qualification_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    site: Mapped[Site] = relationship()

    @property
    def weekday_set(self) -> set[int]:
        return {int(x) for x in self.weekdays.split(",") if x.strip() != ""}


class FixedPost(Base):
    __tablename__ = "fixed_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("coverage_requirements.id"))
    weekdays: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")
    requirement: Mapped[CoverageRequirement] = relationship()


class CompensationTerm(Base):
    """Contractual compensation, effective-dated. Statutory rules live in PayrollRuleSet."""

    __tablename__ = "compensation_terms"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    pay_basis: Mapped[str] = mapped_column(String(10))  # hourly | monthly
    hourly_rate: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    monthly_salary: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    night_premium_pct: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    sunday_holiday_premium_pct: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    overwork_premium_pct: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    overtime_premium_pct: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Holiday(Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("company_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(200))


class SchedulePeriod(Base):
    __tablename__ = "schedule_periods"
    __table_args__ = (UniqueConstraint("company_id", "year", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)  # optimistic lock for draft/publish transitions


VERSION_STATUS = {"draft": "Πρόχειρο", "published": "Δημοσιευμένο", "superseded": "Αντικατεστάθη", "discarded": "Απορρίφθηκε"}


class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"
    __table_args__ = (UniqueConstraint("period_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("schedule_periods.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    base_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedule_versions.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)  # optimistic lock for edits
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    period: Mapped[SchedulePeriod] = relationship()


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("version_id", "slot_key"), Index("ix_assign_emp_start", "employee_id", "start_utc"))
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    slot_key: Mapped[str] = mapped_column(String(80))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    requirement_id: Mapped[int] = mapped_column(ForeignKey("coverage_requirements.id"))
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    service_date: Mapped[date] = mapped_column(Date, index=True)
    start_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    end_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="auto")  # auto | manual | replacement | carried
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    employee: Mapped[Employee] = relationship()
    site: Mapped[Site] = relationship()
    requirement: Mapped[CoverageRequirement] = relationship()


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedule_versions.id"), nullable=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("schedule_periods.id"))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30))
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AttendanceRecord(Base):
    """Actual attendance as reported (append-only; corrections supersede)."""

    __tablename__ = "attendance_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    assignment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    site_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sites.id"), nullable=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    clock_in: Mapped[datetime] = mapped_column(UTCDateTime)
    clock_out: Mapped[datetime] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | superseded | voided
    supersedes_id: Mapped[Optional[int]] = mapped_column(ForeignKey("attendance_records.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    employee: Mapped[Employee] = relationship()


class ApprovedHours(Base):
    """Payable hours approved by an authorised user (append-only; corrections supersede)."""

    __tablename__ = "approved_hours"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    attendance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("attendance_records.id"), nullable=True)
    assignment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    site_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sites.id"), nullable=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    start_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    end_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    basis: Mapped[str] = mapped_column(String(20))  # attendance | plan | manual
    status: Mapped[str] = mapped_column(String(20), default="active")
    supersedes_id: Mapped[Optional[int]] = mapped_column(ForeignKey("approved_hours.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    employee: Mapped[Employee] = relationship()


class PayrollRuleSet(Base):
    __tablename__ = "payroll_rule_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PayrollPeriod(Base):
    __tablename__ = "payroll_periods"
    __table_args__ = (UniqueConstraint("company_id", "year", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | locked
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    locked_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (UniqueConstraint("period_id", "version_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("payroll_periods.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    ruleset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payroll_rule_sets.id"), nullable=True)
    ruleset_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    totals: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PayrollLine(Base):
    __tablename__ = "payroll_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("payroll_runs.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    site_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sites.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(40))
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    rate: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    pct: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    employee: Mapped[Employee] = relationship()


class PayrollEmployeeTotal(Base):
    __tablename__ = "payroll_employee_totals"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("payroll_runs.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    paid_minutes: Mapped[int] = mapped_column(Integer, default=0)
    gross: Mapped[Decimal] = mapped_column(Money)
    employer_contributions: Mapped[Decimal] = mapped_column(Money)
    employee_contributions: Mapped[Decimal] = mapped_column(Money)
    bonus_accrual: Mapped[Decimal] = mapped_column(Money)
    employer_cost: Mapped[Decimal] = mapped_column(Money)
    site_costs: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    employee: Mapped[Employee] = relationship()


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

"""Server-side permission matrix. Every route checks a permission; hiding a
button in the UI is never relied upon."""
from __future__ import annotations

ALL = {"admin", "scheduler", "payroll", "reviewer"}

PERMISSIONS: dict[str, set[str]] = {
    "dashboard.read": ALL,
    "employees.read": ALL,
    "employees.write": {"admin", "scheduler"},
    "compensation.read": {"admin", "payroll"},
    "compensation.write": {"admin", "payroll"},
    "sites.read": ALL,
    "sites.write": {"admin", "scheduler"},
    "schedule.read": ALL,
    "schedule.write": {"admin", "scheduler"},
    "schedule.publish": {"admin", "scheduler"},
    "absences.read": ALL,
    "absences.write": {"admin", "scheduler"},
    "attendance.read": ALL,
    "attendance.write": {"admin", "scheduler", "payroll"},
    "attendance.approve": {"admin", "payroll"},
    "payroll.read": {"admin", "payroll", "reviewer"},
    "payroll.run": {"admin", "payroll"},
    "payroll.lock": {"admin", "payroll"},
    "payroll.reopen": {"admin"},
    "payroll.rules": {"admin"},
    "reports.read": ALL,
    "reports.export": ALL,
    "reports.cost": {"admin", "payroll", "reviewer"},
    "import.run": {"admin", "scheduler"},
    "settings.manage": {"admin"},
    "users.manage": {"admin"},
    "audit.read": {"admin", "reviewer"},
}


def allowed(role: str, perm: str) -> bool:
    if perm not in PERMISSIONS:
        raise KeyError(f"unknown permission {perm}")
    return role in PERMISSIONS[perm]

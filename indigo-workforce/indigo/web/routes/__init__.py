from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from . import absences, attendance, auth, dashboard, employees, payroll, reports, schedule, settings_, sites
    for mod in (auth, dashboard, employees, sites, schedule, absences, attendance, payroll, reports, settings_):
        app.include_router(mod.router)

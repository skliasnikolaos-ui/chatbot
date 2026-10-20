from __future__ import annotations

import logging
import re
import secrets
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode

from .. import db as dbm
from ..authz import allowed
from ..config import settings
from ..migrations import migrate
from ..models import ROLES
from ..services.scope import NotFound
from ..timeutil import fmt_local
from .deps import BadInput, LoginRequired

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
log = logging.getLogger("indigo")

WEEKDAYS = ["Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"]
MONTHS = ["", "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος", "Ιούλιος", "Αύγουστος",
          "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος"]


class ScrubFilter(logging.Filter):
    """Remove personal data patterns (emails, phone numbers, long digit runs) from log records."""

    PATTERNS = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"), (re.compile(r"\+?\d[\d \-]{8,}\d"), "<number>")]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for rx, rep in self.PATTERNS:
            msg = rx.sub(rep, msg)
        record.msg, record.args = msg, ()
        return True


def setup_logging() -> None:
    root = logging.getLogger("indigo")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(ScrubFilter())
        root.addHandler(h)


def money(v) -> str:
    if v is None or v == "":
        return "—"
    v = Decimal(str(v)).quantize(Decimal("0.01"))
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def num(v, places: int = 1) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.{places}f}".replace(",", "X").replace(".", ",").replace("X", ".")


templates.env.filters.update({
    "money": money, "num": num, "lt": lambda dt, f="%d/%m %H:%M": fmt_local(dt, f),
    "dmy": lambda d: d.strftime("%d/%m/%Y") if d else "", "dm": lambda d: d.strftime("%d/%m") if d else "",
    "wd": lambda d: WEEKDAYS[d.weekday()],
})
templates.env.globals.update({"WEEKDAYS": WEEKDAYS, "MONTHS": MONTHS, "ROLES": ROLES, "allowed": allowed,
                              "product_name": settings.product_name})


def render(request: Request, ctx, name: str, status_code: int = 200, **kw) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"ctx": ctx, "msg": request.query_params.get("msg"),
                                                      "err": request.query_params.get("err"), **kw}, status_code=status_code)


def back(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url + (sep + urlencode(params) if params else ""), status_code=303)


def create_app(database_url: str | None = None) -> FastAPI:
    settings.validate()
    setup_logging()
    eng = dbm.init_engine(database_url)
    migrate(eng)
    app = FastAPI(title=settings.product_name, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
        if request.url.path not in ("/login",) and not request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(NotFound)
    async def _nf(request: Request, exc: NotFound):
        return HTMLResponse(_error_page(404, "Δεν βρέθηκε."), status_code=404)

    @app.exception_handler(BadInput)
    async def _bad(request: Request, exc: BadInput):
        ref = request.headers.get("referer") or "/"
        return back(ref.split("?")[0], err=str(exc))

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        return HTMLResponse(_error_page(exc.status_code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _err(request: Request, exc: Exception):
        err_id = secrets.token_hex(4)
        log.exception("unhandled error id=%s path=%s type=%s", err_id, request.url.path, type(exc).__name__)
        return HTMLResponse(_error_page(500, f"Απρόβλεπτο σφάλμα. Κωδικός αναφοράς: {err_id}"), status_code=500)

    from .routes import register
    register(app)
    return app


def _error_page(code: int, text: str) -> str:
    from html import escape
    return (f"<!doctype html><html lang='el'><meta charset='utf-8'><title>{code}</title>"
            f"<link rel='stylesheet' href='/static/style.css'><main class='narrow'><h1>{code}</h1>"
            f"<p>{escape(text)}</p><p><a href='/'>Αρχική</a></p></main></html>")

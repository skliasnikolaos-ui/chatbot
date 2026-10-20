"""Forward-only, additive schema migrations.

Rules: never drop or rewrite existing columns/tables in a migration; add new
ones. Each migration runs once and is recorded in schema_migrations. Take a
backup before migrating (see docs/DEPLOYMENT.md).
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from . import models  # noqa: F401  (register tables)
from .db import Base, utcnow


def _m0001_initial(conn) -> None:
    Base.metadata.create_all(conn, checkfirst=True)


MIGRATIONS = [
    ("0001_initial", _m0001_initial),
]


def applied(engine: Engine) -> set[str]:
    if not inspect(engine).has_table("schema_migrations"):
        return set()
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("SELECT version FROM schema_migrations"))}


def migrate(engine: Engine) -> list[str]:
    done = applied(engine)
    ran = []
    for version, fn in MIGRATIONS:
        if version in done:
            continue
        with engine.begin() as conn:
            fn(conn)
            models.SchemaMigration.__table__.create(conn, checkfirst=True)
            conn.execute(models.SchemaMigration.__table__.insert().values(version=version, applied_at=utcnow()))
        ran.append(version)
    return ran

import os

import pytest

os.environ.setdefault("INDIGO_SOLVER_WORKERS", "2")

from indigo import db as dbm  # noqa: E402
from indigo.migrations import migrate  # noqa: E402

PASSWORD = "test-password-123"


@pytest.fixture()
def engine(tmp_path):
    url = os.environ.get("INDIGO_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.sqlite3"
    eng = dbm.init_engine(url)
    if url.startswith("postgresql"):
        from indigo.db import Base
        Base.metadata.drop_all(eng)
        from sqlalchemy import text
        with eng.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS schema_migrations"))
    migrate(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    s = dbm.get_session()
    yield s
    s.close()


@pytest.fixture()
def demo(db):
    from indigo.services.demo import seed
    return seed(db, 2026, 11, PASSWORD, n_employees=24)

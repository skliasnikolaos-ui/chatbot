"""Command line: python -m indigo.cli <command>

  migrate                         apply pending schema migrations
  create-company NAME ADMIN_EMAIL ADMIN_NAME   (password read from INDIGO_ADMIN_PASSWORD or prompt)
  seed-demo YEAR MONTH            synthetic demo company (password from INDIGO_DEMO_PASSWORD or prompt)
  backup DEST                     SQLite online backup (for PostgreSQL use pg_dump, see docs/DEPLOYMENT.md)
  pilot YEAR MONTH                run the scripted pilot workflow on the demo company and print a report
"""
from __future__ import annotations

import getpass
import os
import sqlite3
import sys

from . import db as dbm
from .config import settings
from .migrations import migrate


def _password(env: str) -> str:
    pw = os.environ.get(env) or getpass.getpass("Κωδικός (≥10 χαρακτήρες): ")
    return pw


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    eng = dbm.init_engine()
    if cmd == "migrate":
        ran = migrate(eng)
        print("Εφαρμόστηκαν:", ", ".join(ran) if ran else "καμία (ενημερωμένο)")
        return 0
    migrate(eng)
    if cmd == "create-company":
        from .services.companies import create_company, create_user
        name, email, admin_name = args
        with dbm.get_session() as s:
            c = create_company(s, name)
            create_user(s, c.id, email, admin_name, "admin", _password("INDIGO_ADMIN_PASSWORD"))
            s.commit()
            print(f"Εταιρεία #{c.id} δημιουργήθηκε· διαχειριστής {email}")
        return 0
    if cmd == "seed-demo":
        from .services.demo import seed
        year, month = int(args[0]), int(args[1])
        with dbm.get_session() as s:
            out = seed(s, year, month, _password("INDIGO_DEMO_PASSWORD"))
        print(f"Demo εταιρεία #{out['company_id']} (ΣΥΝΘΕΤΙΚΑ ΔΕΔΟΜΕΝΑ). Χρήστες: {', '.join(out['users'].values())}")
        return 0
    if cmd == "backup":
        if not settings.database_url.startswith("sqlite"):
            print("Για PostgreSQL χρησιμοποιήστε pg_dump (docs/DEPLOYMENT.md).")
            return 2
        src = sqlite3.connect(settings.database_url.split("///", 1)[1])
        dst = sqlite3.connect(args[0])
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        print("Αντίγραφο ασφαλείας:", args[0])
        return 0
    if cmd == "pilot":
        from .pilot import run_pilot
        print(run_pilot(int(args[0]), int(args[1])))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

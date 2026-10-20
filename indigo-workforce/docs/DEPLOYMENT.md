# Deployment, backup/restore and rollback

## Environments

Use a separate database per environment (dev, pilot, production). **Never point
development or tests at a production database** — the test suite wipes the
database given in `INDIGO_TEST_DATABASE_URL`.

Configuration is environment-only (see `.env.example`):

| Variable | Meaning |
|---|---|
| `INDIGO_DATABASE_URL` | `sqlite:///var/indigo.sqlite3` or `postgresql+psycopg://user:pass@host/db` |
| `INDIGO_SECRET_KEY` | ≥32 random chars; required in production (session token HMAC) |
| `INDIGO_ENV` | `production` enables startup checks |
| `INDIGO_COOKIE_SECURE` | `1` behind HTTPS (required in production) |
| `INDIGO_PRODUCT_NAME` | displayed product name |
| `INDIGO_LOG_FILE` | log path (PII-scrubbed) |
| `INDIGO_SOLVER_WORKERS` | CP-SAT threads per generation |

Secrets go in the hosting platform's secret store or an `.env` file readable
only by the service account; never commit them.

## Pilot deployment with Docker Compose (PostgreSQL)

```bash
cd indigo-workforce
export POSTGRES_PASSWORD=$(openssl rand -base64 24)
export INDIGO_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
docker compose up -d --build                         # runs migrations on start
docker compose exec app python -m indigo.cli create-company "Επωνυμία" admin@example.gr "Ονοματεπώνυμο"
```

The app listens on `127.0.0.1:8000`; terminate TLS in a reverse proxy
(nginx/Caddy) and add request rate limiting for `/login` there.
(The Dockerfile and compose file are **not yet verified**: a build was attempted
in the development environment but Docker Hub refused the `python:3.13-slim`
base-image pull with `429 Too Many Requests`. Build and smoke-test on the target
host before relying on them. The same commands run outside Docker were verified.)

## Plain host (systemd)

```bash
python3 -m venv /opt/indigo/.venv && /opt/indigo/.venv/bin/pip install -r requirements.txt "psycopg[binary]==3.2.13"
INDIGO_ENV=production … /opt/indigo/.venv/bin/python -m indigo.cli migrate
INDIGO_ENV=production … /opt/indigo/.venv/bin/uvicorn --factory indigo.web.app:create_app --host 127.0.0.1 --port 8000 --workers 2
```

## Migrations

`python -m indigo.cli migrate` applies pending, forward-only, additive
migrations recorded in `schema_migrations` (also run at app start). Always
back up before upgrading. Migrations never drop or rewrite data.

## Backup

* **SQLite**: `python -m indigo.cli backup /backups/indigo-$(date +%F).sqlite3` (online, consistent copy).
* **PostgreSQL**: `pg_dump --format=custom --file=/backups/indigo-$(date +%F).dump "$DATABASE"`
  (schedule daily; keep ≥30 days; copy off-host; encrypt at rest — the data contains employee personal data).

## Restore

* SQLite: stop the app, replace the database file with the backup, start the app.
* PostgreSQL: `createdb indigo_restore && pg_restore --dbname=indigo_restore /backups/….dump`, verify, then switch
  `INDIGO_DATABASE_URL` (or `pg_restore --clean` into the original database during a maintenance window).
* Test a restore at least once before the pilot starts and after each upgrade.

## Rollback

1. Stop the app.
2. Re-deploy the previous image/commit.
3. If the failed release applied a migration, restore the pre-upgrade backup (migrations are additive, so the
   previous code normally runs on the newer schema; restore only if data was affected).
4. Start the app and check `/login`, the dashboard and one schedule page.

## Monitoring

Logs go to stdout and `INDIGO_LOG_FILE`. Messages are filtered for e-mail
addresses and long number sequences; unhandled errors are logged with a short
error id that the user sees instead of details. Ship logs to the operator's
log platform; alert on `ERROR` lines. No third-party error service is bundled.

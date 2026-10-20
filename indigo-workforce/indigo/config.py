"""Runtime configuration, read from environment variables only.

No secret has a usable default in production: `Settings.validate()` refuses
to start with the development secret when INDIGO_ENV=production.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEV_SECRET = "dev-only-insecure-secret-change-me"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class Settings:
    product_name: str = field(default_factory=lambda: _env("INDIGO_PRODUCT_NAME", "INDIGO WORKFORCE"))
    database_url: str = field(default_factory=lambda: _env("INDIGO_DATABASE_URL", "sqlite:///var/indigo.sqlite3"))
    secret_key: str = field(default_factory=lambda: _env("INDIGO_SECRET_KEY", DEV_SECRET))
    cookie_secure: bool = field(default_factory=lambda: _env("INDIGO_COOKIE_SECURE", "0") == "1")
    env: str = field(default_factory=lambda: _env("INDIGO_ENV", "development"))
    log_file: str | None = field(default_factory=lambda: _env("INDIGO_LOG_FILE", None))
    solver_workers: int = field(default_factory=lambda: int(_env("INDIGO_SOLVER_WORKERS", "4")))
    session_hours: int = field(default_factory=lambda: int(_env("INDIGO_SESSION_HOURS", "12")))
    timezone: str = "Europe/Athens"

    def validate(self) -> None:
        if self.env == "production":
            if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
                raise RuntimeError("INDIGO_SECRET_KEY must be set to a random value of 32+ characters in production")
            if not self.cookie_secure:
                raise RuntimeError("INDIGO_COOKIE_SECURE=1 is required in production (serve over HTTPS)")


settings = Settings()

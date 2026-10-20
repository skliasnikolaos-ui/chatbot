"""Authentication: Argon2 password hashing, server-side revocable sessions,
per-session CSRF tokens, and login throttling."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import utcnow
from .models import User, UserSession

_ph = PasswordHasher()
MAX_FAILED = 5
LOCK_MINUTES = 15
MIN_PASSWORD = 10


def hash_password(pw: str) -> str:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"Ο κωδικός πρέπει να έχει τουλάχιστον {MIN_PASSWORD} χαρακτήρες")
    return _ph.hash(pw)


def _token_hash(token: str) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    now = utcnow()
    if user is None:
        _ph.hash(password or "x")  # equalise timing
        return None
    if not user.is_active or (user.locked_until and user.locked_until > now):
        return None
    try:
        _ph.verify(user.password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        db.commit()
        return None
    user.failed_logins = 0
    user.locked_until = None
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = _ph.hash(password)
    db.commit()
    return user


def create_session(db: Session, user: User) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    s = UserSession(user_id=user.id, token_hash=_token_hash(token), csrf_token=secrets.token_urlsafe(32),
                    expires_at=utcnow() + timedelta(hours=settings.session_hours))
    db.add(s)
    db.commit()
    return token, s


def lookup_session(db: Session, token: str | None) -> tuple[User, UserSession] | None:
    if not token:
        return None
    s = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if s is None or s.revoked or s.expires_at <= utcnow():
        return None
    user = db.get(User, s.user_id)
    if user is None or not user.is_active:
        return None
    return user, s


def revoke_session(db: Session, token: str | None) -> None:
    found = lookup_session(db, token)
    if found:
        found[1].revoked = True
        db.commit()


def revoke_all_for_user(db: Session, user_id: int) -> None:
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked.is_(False))):
        s.revoked = True


def csrf_ok(sess: UserSession, submitted: str | None) -> bool:
    return bool(submitted) and hmac.compare_digest(sess.csrf_token, submitted)

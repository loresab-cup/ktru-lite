"""Accounts of the site: users with scrypt password hashes and login sessions in the database.

Anyone can register with a login and a password and is logged in at once. A session is a random token in an HttpOnly cookie; the database keeps only its SHA-256, so a
leaked table does not let anyone in. The first admin comes from ADMIN_LOGIN and ADMIN_PASSWORD
when there are no users yet; `python users.py` manages accounts from the command line.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, String, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# scrypt with 16 MB of memory per check: ~50 ms, slow enough against guessing a stolen hash
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD = 8
LOGIN_RE = re.compile(r"[a-z0-9._-]{3,32}")
# Wrong passwords for one login from one address: after MAX_FAILS the login waits LOCK_SECONDS
MAX_FAILS = 5
LOCK_SECONDS = 60.0


class LoginTaken(ValueError):
    """Another user already has this login."""


class WrongPassword(ValueError):
    """The current password did not authorize an account change."""


class AuthBase(DeclarativeBase):
    pass


class User(AuthBase):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LoginSession(AuthBase):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    parts = ["scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P), _b64(salt), _b64(digest)]
    return "$".join(parts)


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        found = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except ValueError:
        return False
    return hmac.compare_digest(found, base64.b64decode(digest))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=2**26)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# Checked when the login does not exist, so a wrong login takes as long as a wrong password
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def clean_login(login: str) -> str:
    """Lowercase login; ValueError when it has other characters or another length."""
    login = login.strip().lower()
    if not LOGIN_RE.fullmatch(login):
        raise ValueError(
            "Логин: от 3 до 32 знаков, латинские буквы, цифры, точка, дефис или подчёркивание"
        )
    return login


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Пароль: не короче {MIN_PASSWORD} знаков")


def create_user(session: Session, login: str, password: str, is_admin: bool = False) -> User:
    login = clean_login(login)
    check_password(password)
    if session.scalar(select(User).where(User.login == login)):
        raise LoginTaken(f"Логин {login} уже занят")
    user = User(login=login, password_hash=hash_password(password), is_admin=is_admin)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:  # the same login registered a moment ago by another request
        session.rollback()
        raise LoginTaken(f"Логин {login} уже занят") from None
    return user


def set_password(session: Session, login: str, password: str) -> None:
    """A new password also ends every session of the user."""
    check_password(password)
    user = _user(session, login)
    user.password_hash = hash_password(password)
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session.commit()


def remove_user(session: Session, login: str) -> None:
    user = _user(session, login)
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session.delete(user)
    session.commit()


def locked_user(session: Session, token: str) -> User | None:
    """Lock an authenticated account and recheck a token after waiting for concurrent changes."""
    found = user_by_token(session, token)
    if found is None:
        return None
    user = session.scalar(
        select(User)
        .where(User.id == found.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # A concurrent profile change may have revoked this token while the row lock was awaited.
    active = session.scalar(
        select(LoginSession.token_hash).where(
            LoginSession.token_hash == _token_hash(token),
            LoginSession.expires_at > datetime.now(UTC),
        )
    )
    return user if active is not None else None


def change_profile(
    session: Session,
    token: str,
    current_password: str,
    login: str | None,
    password: str | None,
    hours: float,
) -> tuple[str, User] | None:
    """Atomically change this account and rotate all sessions; never accept another user ID."""
    user = locked_user(session, token)
    if user is None:
        return None
    if not verify_password(current_password, user.password_hash):
        raise WrongPassword("Текущий пароль неверен")
    if login is None and password is None:
        raise ValueError("Укажите новый логин или пароль")
    if login is not None:
        user.login = clean_login(login)
    if password is not None:
        check_password(password)
        user.password_hash = hash_password(password)
    try:
        session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        fresh = start_session(session, user, hours)
    except IntegrityError:
        session.rollback()
        raise LoginTaken("Этот логин уже занят") from None
    return fresh, user


def _user(session: Session, login: str) -> User:
    user = session.scalar(select(User).where(User.login == clean_login(login)))
    if user is None:
        raise ValueError(f"Пользователя {login} нет")
    return user


def ensure_admin(session: Session, login: str, password: str) -> bool:
    """Creates the first admin when there are no users and both values are given."""
    if not login or not password or session.scalar(select(func.count(User.id))):
        return False
    create_user(session, login, password, is_admin=True)
    return True


def has_users(session: Session) -> bool:
    return bool(session.scalar(select(func.count(User.id))))


def log_in(session: Session, login: str, password: str, hours: float) -> tuple[str, User] | None:
    """(token, user) for the right password, None otherwise."""
    try:
        login = clean_login(login)
    except ValueError:
        login = ""
    user = session.scalar(select(User).where(User.login == login)) if login else None
    if not verify_password(password, user.password_hash if user else _DUMMY_HASH) or not user:
        return None
    return start_session(session, user, hours), user


def register(session: Session, login: str, password: str, hours: float) -> tuple[str, User]:
    """A new ordinary user, logged in at once; ValueError (LoginTaken) says what is wrong."""
    user = create_user(session, login, password)
    return start_session(session, user, hours), user


def start_session(session: Session, user: User, hours: float) -> str:
    """A new session token; sessions that are over are removed here."""
    now = datetime.now(UTC)
    session.execute(delete(LoginSession).where(LoginSession.expires_at < now))
    token = secrets.token_urlsafe(32)
    session.add(
        LoginSession(
            token_hash=_token_hash(token), user_id=user.id, expires_at=now + timedelta(hours=hours)
        )
    )
    session.commit()
    return token


def user_by_token(session: Session, token: str) -> User | None:
    if not token:
        return None
    row = session.get(LoginSession, _token_hash(token))
    if row is None or _aware(row.expires_at) < datetime.now(UTC):
        return None
    return session.get(User, row.user_id)


def log_out(session: Session, token: str) -> None:
    if token:
        session.execute(delete(LoginSession).where(LoginSession.token_hash == _token_hash(token)))
        session.commit()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(moment: datetime) -> datetime:
    """SQLite gives back naive datetimes; they were written in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class Throttle:
    """Counts wrong passwords per (login, address); blocks after MAX_FAILS for LOCK_SECONDS."""

    def __init__(self) -> None:
        self._fails: dict[tuple[str, str], tuple[int, float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: tuple[str, str]) -> bool:
        with self._lock:
            count, until = self._fails.get(key, (0, 0.0))
            return count >= MAX_FAILS and time.monotonic() < until

    def failed(self, key: tuple[str, str]) -> None:
        with self._lock:
            count, until = self._fails.get(key, (0, 0.0))
            if count >= MAX_FAILS and time.monotonic() >= until:
                count = 0  # the pause is over: a new round of attempts
            self._fails[key] = (count + 1, time.monotonic() + LOCK_SECONDS)
            if len(self._fails) > 10_000:  # many addresses: forget the pauses that are over
                now = time.monotonic()
                self._fails = {k: v for k, v in self._fails.items() if v[1] > now}

    def passed(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._fails.pop(key, None)

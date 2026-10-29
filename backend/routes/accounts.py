"""Login, registration, logout and session endpoints."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

import auth
import database as db
import privacy
import runtime as rt

router = APIRouter()


throttle = auth.Throttle()  # wrong passwords


registrations = auth.Throttle()  # new accounts from one address: MAX_FAILS, then a pause
guests = auth.Throttle(limit=30)  # a class behind one address may open the site at once
profile_attempts = auth.Throttle()


def session_user(token: str) -> auth.User | None:
    with db.SessionLocal() as session:
        return auth.user_by_token(session, token)


class PolicyIn(BaseModel):
    """The checkbox of the entry page: the privacy policy and the files consent."""

    privacy_version: str = Field(default="", max_length=32)
    privacy_accepted: bool = False


class LoginIn(PolicyIn):
    login: str = Field(max_length=64)
    password: str = Field(max_length=200)


def require_policy(body: PolicyIn) -> None:
    if not body.privacy_accepted or body.privacy_version != privacy.VERSION:
        raise HTTPException(
            400, "Примите политику конфиденциальности и согласие на обработку файлов"
        )


def user_out(user: auth.User) -> dict:
    return {"login": user.login, "is_admin": user.is_admin, "is_guest": user.is_guest}


@router.post("/api/auth/login")
def log_in(body: LoginIn, request: Request, response: Response):
    key = (body.login.strip().lower(), client_address(request))
    require_policy(body)
    if throttle.blocked(key):
        raise HTTPException(429, "Слишком много неудачных попыток: подождите минуту")
    with db.SessionLocal() as session:
        found = auth.log_in(session, body.login, body.password, rt.SESSION_HOURS)
        if found is not None:
            privacy.accept_entry(session, found[1].id, body.privacy_version)
            auth.adopt_guest(session, request.cookies.get(rt.COOKIE, ""), found[1])
    if found is None:
        throttle.failed(key)
        raise HTTPException(401, "Неверный логин или пароль")
    throttle.passed(key)
    return with_session(response, *found)


@router.post("/api/auth/register", status_code=201)
def register(body: LoginIn, request: Request, response: Response):
    """A new account by login and password; the user is logged in at once."""
    key = ("", client_address(request))
    require_policy(body)
    if registrations.blocked(key):
        raise HTTPException(
            429,
            "С этого адреса только что зарегистрировали несколько учётных записей: подождите минуту",
        )
    with db.SessionLocal() as session:
        try:
            found = auth.register(session, body.login, body.password, rt.SESSION_HOURS)
            privacy.accept_entry(session, found[1].id, body.privacy_version)
            auth.adopt_guest(session, request.cookies.get(rt.COOKIE, ""), found[1])
        except auth.LoginTaken as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    registrations.failed(key)  # counts accounts made from the address
    return with_session(response, *found)


@router.post("/api/auth/guest", status_code=201)
def guest(body: PolicyIn, request: Request, response: Response):
    """Work without an account after accepting the privacy policy; the files of the guest live
    while the browser is open (the cookie has no expiry date)."""
    key = ("guest", client_address(request))
    require_policy(body)
    if guests.blocked(key):
        raise HTTPException(
            429, "С этого адреса только что открыли много сеансов: подождите минуту"
        )
    with db.SessionLocal() as session:
        found = auth.start_guest(session, rt.GUEST_HOURS)
        privacy.accept_entry(session, found[1].id, body.privacy_version)
    guests.failed(key)
    return with_session(response, *found)


def with_session(response: Response, token: str, user: auth.User) -> dict:
    response.set_cookie(
        rt.COOKIE,
        token,
        # a guest's cookie ends with the browser, an account's with its session
        max_age=None if user.is_guest else int(rt.SESSION_HOURS * 3600),
        httponly=True,
        samesite="lax",
        secure=rt.COOKIE_SECURE,
        path="/",
    )
    return user_out(user)


def client_address(request: Request) -> str:
    """The address nginx saw: it appends it last to X-Forwarded-For; the backend port is local."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    return forwarded or (request.client.host if request.client else "")


@router.post("/api/auth/logout", status_code=204)
def log_out(request: Request, response: Response):
    with db.SessionLocal() as session:
        auth.log_out(session, request.cookies.get(rt.COOKIE, ""))
    response.delete_cookie(rt.COOKIE, path="/")


@router.get("/api/auth/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:  # rt.AUTH_REQUIRED=0: nobody logged in, everything is open
        return {"login": "", "is_admin": False, "is_guest": False}
    return user_out(user)


class ProfileIn(BaseModel):
    model_config = {"extra": "forbid"}
    current_password: str = Field(min_length=1, max_length=200)
    login: str | None = Field(default=None, max_length=64)
    password: str | None = Field(default=None, max_length=200)


@router.patch("/api/auth/profile")
def change_profile(body: ProfileIn, request: Request, response: Response):
    user = getattr(request.state, "user", None)
    key = (f"profile:{user.id if user else 'anonymous'}", client_address(request))
    if profile_attempts.blocked(key):
        raise HTTPException(429, "Слишком много неудачных попыток: подождите минуту")
    with db.SessionLocal() as session:
        try:
            found = auth.change_profile(
                session,
                request.cookies.get(rt.COOKIE, ""),
                body.current_password,
                body.login,
                body.password,
                rt.SESSION_HOURS,
            )
        except auth.WrongPassword as exc:
            profile_attempts.failed(key)
            raise HTTPException(403, str(exc)) from None
        except auth.LoginTaken as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    if found is None:
        raise HTTPException(401, "Войдите в систему")
    profile_attempts.passed(key)
    return with_session(response, *found)


def auth_state() -> str:
    """on: a login is needed (anyone can register); off: rt.AUTH_REQUIRED=0."""
    return "on" if rt.AUTH_REQUIRED else "off"

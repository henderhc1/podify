from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import Header, HTTPException, Request, Response

from podify.config import get_admin_token, is_email_verification_required
from podify.services.users import find_user, normalize_email, public_user
from podify.state import utc_now

ACCESS_SESSION_COOKIE = "podify_access"
ACCESS_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def access_signup_prompt() -> str:
    if is_email_verification_required():
        return "Verify your email before using Spreview."
    return "Sign up with a valid email before using Spreview."


def hash_access_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_verification_token(user: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(24)
    now = utc_now()
    user["verification_token_hash"] = hash_verification_token(token)
    user["verification_token"] = None
    user["requested_at"] = now
    user["updated_at"] = now
    return token


def issue_access_session(user: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    user["session_token_hash"] = hash_access_session_token(token)
    user["session_issued_at"] = now
    user["session_last_seen_at"] = now
    return token


def clear_access_session(user: dict[str, Any]) -> None:
    user["session_token_hash"] = None
    user["session_issued_at"] = None
    user["session_last_seen_at"] = None


def apply_access_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        key=ACCESS_SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=ACCESS_SESSION_MAX_AGE_SECONDS,
        path="/",
    )


def clear_access_session_cookie(response: Response) -> None:
    response.delete_cookie(key=ACCESS_SESSION_COOKIE, path="/")


def find_user_by_session_token(state: dict[str, Any], token: str) -> dict[str, Any] | None:
    token_hash = hash_access_session_token(token)
    for candidate in state["users"]:
        stored_hash = candidate.get("session_token_hash")
        if stored_hash and hmac.compare_digest(str(stored_hash), token_hash):
            return candidate
    return None


def get_session_user(state: dict[str, Any], request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(ACCESS_SESSION_COOKIE, "").strip()
    if not token:
        return None
    return find_user_by_session_token(state, token)


def describe_access_state(state: dict[str, Any], request: Request) -> dict[str, Any]:
    user = get_session_user(state, request)
    verification_required = is_email_verification_required()
    if not user:
        return {
            "authenticated": False,
            "service_access": False,
            "reason": access_signup_prompt(),
            "user": None,
        }

    normalized_email = normalize_email(user.get("email", ""))
    blocked = normalized_email in state["blocked_emails"] or user.get("status") == "blocked"
    service_access = (
        user.get("status") == "active"
        and not blocked
        and (bool(user.get("email_verified")) or not verification_required)
    )

    reason = ""
    if blocked:
        reason = "This account has been blocked."
    elif verification_required and not user.get("email_verified"):
        reason = "Verify your email before using Spreview."
    elif user.get("status") == "waitlisted":
        if verification_required:
            reason = "Your email is verified, but your account is still waitlisted."
        else:
            reason = "Your signup is valid, but your account is still waitlisted."
    elif user.get("status") != "active":
        reason = "An active Spreview account is required before using the service."

    return {
        "authenticated": True,
        "service_access": service_access,
        "reason": reason,
        "user": public_user(user),
    }


def require_active_user(request: Request) -> dict[str, Any]:
    from podify.state import load_state

    state = load_state()
    user = get_session_user(state, request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"{access_signup_prompt()} An active Spreview account is required before using the service.",
        )

    normalized_email = normalize_email(user.get("email", ""))
    if normalized_email in state["blocked_emails"] or user.get("status") == "blocked":
        raise HTTPException(status_code=403, detail="This account has been blocked.")
    if is_email_verification_required() and not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Verify your email before using Spreview.")
    if user.get("status") == "waitlisted":
        raise HTTPException(
            status_code=403,
            detail=(
                "Your email is verified, but your account is still waitlisted."
                if is_email_verification_required()
                else "Your signup is valid, but your account is still waitlisted."
            ),
        )
    if user.get("status") != "active":
        raise HTTPException(
            status_code=403,
            detail="An active Spreview account is required before using the service.",
        )
    return user

def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    admin_token = get_admin_token()
    if not admin_token:
        raise HTTPException(
            status_code=503,
            detail="Admin API is disabled until PODIFY_ADMIN_TOKEN is configured.",
        )
    provided_token = str(x_admin_token or "").strip()
    if not provided_token or not hmac.compare_digest(provided_token, admin_token):
        raise HTTPException(status_code=401, detail="A valid admin token is required.")

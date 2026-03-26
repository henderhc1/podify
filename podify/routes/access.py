from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, Response

from podify.auth import (
    ACCESS_SESSION_COOKIE,
    apply_access_session_cookie,
    clear_access_session,
    clear_access_session_cookie,
    find_user_by_session_token,
    issue_access_session,
)
from podify.config import get_max_active_users, is_demo_verification_enabled
from podify.services.users import (
    active_user_count,
    ensure_not_blocked_email,
    find_user,
    public_user,
    validate_email,
)
from podify.state import STATE_LOCK, load_state_unlocked, save_state_unlocked, utc_now

router = APIRouter()


def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_user_by_verification_token(state: dict[str, Any], token: str) -> dict[str, Any] | None:
    token_hash = hash_verification_token(token)
    for candidate in state["users"]:
        stored_hash = candidate.get("verification_token_hash")
        if stored_hash and hmac.compare_digest(str(stored_hash), token_hash):
            return candidate

        # Backward-compatible fallback for older state files.
        stored_plaintext = candidate.get("verification_token")
        if stored_plaintext and hmac.compare_digest(str(stored_plaintext), token):
            return candidate
    return None


@router.post("/register")
async def request_access(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    email = validate_email(payload.get("email", ""))

    with STATE_LOCK:
        state = load_state_unlocked()
        ensure_not_blocked_email(state, email)
        user = find_user(state, email)
        now = utc_now()
        token = secrets.token_urlsafe(24)
        already_verified = bool(user and user.get("email_verified"))
        if not user:
            user = {
                "email": email,
                "created_at": now,
            }
            state["users"].append(user)

        user["verification_token_hash"] = hash_verification_token(token)
        user["verification_token"] = None
        user["requested_at"] = now
        user["updated_at"] = now
        if not already_verified:
            user["status"] = "pending_verification"
            user["email_verified"] = False
        save_state_unlocked(state)

    if is_demo_verification_enabled():
        return {
            "status": "pending_verification",
            "message": (
                "Verification is required before service access is granted. Demo verification "
                "is enabled in this environment, so the token is exposed for local testing."
            ),
            "verification_url": f"/register/verify?token={token}",
            "verification_token": token,
        }

    return {
        "status": "pending_verification",
        "message": (
            "Verification is required before service access is granted. Demo verification is "
            "disabled in secure mode, so the token is not exposed by the API."
        ),
    }


@router.get("/register/verify")
async def verify_access_request(token: str, response: Response, request: Request) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=400, detail="A verification token is required.")

    with STATE_LOCK:
        state = load_state_unlocked()
        user = find_user_by_verification_token(state, token)
        if not user:
            raise HTTPException(status_code=404, detail="Verification token not found.")

        ensure_not_blocked_email(state, user["email"])
        user["email_verified"] = True
        user["verified_at"] = utc_now()
        user["verification_token_hash"] = None
        user["verification_token"] = None
        if user.get("status") == "active" or active_user_count(state) < get_max_active_users():
            user["status"] = "active"
            message = "Email verified. Access is now active."
        else:
            user["status"] = "waitlisted"
            message = "Email verified, but the 1,000-user cap has been reached. You are waitlisted."
        user["updated_at"] = utc_now()
        session_token = issue_access_session(user)
        save_state_unlocked(state)

        apply_access_session_cookie(response, session_token, secure=request.url.scheme == "https")
        return {
            "status": user["status"],
            "message": message,
            "user": public_user(user),
            "registration_open": active_user_count(state) < get_max_active_users(),
        }


@router.post("/session/logout")
async def logout_access_session(response: Response, request: Request) -> dict[str, str]:
    with STATE_LOCK:
        state = load_state_unlocked()
        user = find_user_by_session_token(
            state,
            request.cookies.get(ACCESS_SESSION_COOKIE, "").strip(),
        )
        if user:
            clear_access_session(user)
            user["updated_at"] = utc_now()
            save_state_unlocked(state)

    clear_access_session_cookie(response)
    return {"status": "signed_out"}

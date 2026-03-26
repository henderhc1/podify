from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if not normalized or not EMAIL_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="A valid email address is required.")
    return normalized


def active_user_count(state: dict[str, Any]) -> int:
    return sum(1 for user in state["users"] if user.get("status") == "active")


def find_user(state: dict[str, Any], email: str) -> dict[str, Any] | None:
    normalized = normalize_email(email)
    return next((user for user in state["users"] if user.get("email") == normalized), None)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": user.get("email", ""),
        "status": user.get("status", "pending_verification"),
        "email_verified": bool(user.get("email_verified")),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "verified_at": user.get("verified_at"),
        "requested_at": user.get("requested_at"),
    }


def ensure_not_blocked_email(state: dict[str, Any], email: str) -> None:
    if normalize_email(email) in state["blocked_emails"]:
        raise HTTPException(status_code=403, detail="This email address is blocked.")


def sort_users(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {"active": 0, "waitlisted": 1, "pending_verification": 2, "blocked": 3}
    return sorted(
        users,
        key=lambda item: (
            status_order.get(item.get("status", "pending_verification"), 99),
            item.get("email", ""),
        ),
    )

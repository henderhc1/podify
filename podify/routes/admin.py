from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from podify.auth import clear_access_session, issue_verification_token, require_admin
from podify.config import (
    clear_ytdlp_runtime_cookie_file,
    get_max_active_users,
    get_ytdlp_cookie_status,
    save_ytdlp_runtime_cookie_text,
)
from podify.services.users import (
    active_user_count,
    ensure_not_blocked_email,
    find_user,
    public_user,
    sort_users,
    validate_email,
)
from podify.services.videos import block_video_in_state, clear_playback_cache, extract_video_id
from podify.state import STATE_LOCK, load_state, load_state_unlocked, save_state_unlocked, utc_now

router = APIRouter()


@router.get("/admin/summary", dependencies=[Depends(require_admin)])
async def get_admin_summary() -> dict[str, Any]:
    state = load_state()
    return {
        "active_user_count": active_user_count(state),
        "max_active_users": get_max_active_users(),
        "registration_open": active_user_count(state) < get_max_active_users(),
        "waitlisted_user_count": sum(1 for user in state["users"] if user.get("status") == "waitlisted"),
        "blocked_email_count": len(state["blocked_emails"]),
        "dmca_blocked_video_count": len(state["blocked_videos"]),
        "library_item_count": len(state["library_items"]),
    }


@router.get("/admin/users", dependencies=[Depends(require_admin)])
async def get_admin_users() -> list[dict[str, Any]]:
    state = load_state()
    return [public_user(user) for user in sort_users(state["users"])]


@router.get("/admin/blocked-emails", dependencies=[Depends(require_admin)])
async def get_admin_blocked_emails() -> list[str]:
    state = load_state()
    return sorted(state["blocked_emails"])


@router.get("/admin/dmca/notices", dependencies=[Depends(require_admin)])
async def get_admin_dmca_notices() -> list[dict[str, Any]]:
    state = load_state()
    return state["dmca_notices"]


@router.get("/admin/ytdlp/cookies", dependencies=[Depends(require_admin)])
async def get_admin_ytdlp_cookie_status() -> dict[str, Any]:
    return get_ytdlp_cookie_status()


@router.post("/admin/ytdlp/cookies", dependencies=[Depends(require_admin)])
async def admin_set_ytdlp_cookies(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    cookie_text = str(payload.get("cookie_text") or "")
    try:
        cookie_file = save_ytdlp_runtime_cookie_text(cookie_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cleared_cache_entries = clear_playback_cache()
    return {
        "status": "saved",
        "cookie_file": cookie_file,
        "cleared_cache_entries": cleared_cache_entries,
        **get_ytdlp_cookie_status(),
    }


@router.delete("/admin/ytdlp/cookies", dependencies=[Depends(require_admin)])
async def admin_clear_ytdlp_cookies() -> dict[str, Any]:
    removed = clear_ytdlp_runtime_cookie_file()
    cleared_cache_entries = clear_playback_cache()
    return {
        "status": "cleared",
        "removed": removed,
        "cleared_cache_entries": cleared_cache_entries,
        **get_ytdlp_cookie_status(),
    }


@router.post("/admin/users", dependencies=[Depends(require_admin)])
async def admin_add_user(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    email = validate_email(payload.get("email", ""))
    requested_status = str(payload.get("status") or "active").strip().lower()
    if requested_status not in {"active", "waitlisted"}:
        raise HTTPException(status_code=422, detail="Status must be 'active' or 'waitlisted'.")

    with STATE_LOCK:
        state = load_state_unlocked()
        ensure_not_blocked_email(state, email)
        user = find_user(state, email)
        if not user:
            user = {
                "email": email,
                "created_at": utc_now(),
            }
            state["users"].append(user)

        if requested_status == "active" and user.get("status") != "active":
            if active_user_count(state) >= get_max_active_users():
                raise HTTPException(status_code=409, detail="The 1,000-user cap has been reached.")

        user.update(
            {
                "status": requested_status,
                "email_verified": True,
                "verified_at": user.get("verified_at") or utc_now(),
                "verification_token_hash": None,
                "verification_token": None,
                "updated_at": utc_now(),
            }
        )
        save_state_unlocked(state)
        return {"status": "saved", "user": public_user(user)}


@router.post("/admin/users/approve", dependencies=[Depends(require_admin)])
async def admin_approve_user(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    email = validate_email(payload.get("email", ""))

    with STATE_LOCK:
        state = load_state_unlocked()
        ensure_not_blocked_email(state, email)
        user = find_user(state, email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        if user.get("status") != "active" and active_user_count(state) >= get_max_active_users():
            raise HTTPException(status_code=409, detail="The 1,000-user cap has been reached.")

        user.update(
            {
                "status": "active",
                "email_verified": True,
                "verified_at": user.get("verified_at") or utc_now(),
                "verification_token_hash": None,
                "verification_token": None,
                "updated_at": utc_now(),
            }
        )
        save_state_unlocked(state)
        return {"status": "approved", "user": public_user(user)}


@router.post("/admin/users/access-link", dependencies=[Depends(require_admin)])
async def admin_create_access_link(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    email = validate_email(payload.get("email", ""))

    with STATE_LOCK:
        state = load_state_unlocked()
        ensure_not_blocked_email(state, email)
        user = find_user(state, email)
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        if not user.get("email_verified") or user.get("status") != "active":
            raise HTTPException(
                status_code=409,
                detail="Only active verified users can receive a test access link.",
            )

        token = issue_verification_token(user)
        save_state_unlocked(state)
        return {
            "status": "created",
            "email": email,
            "access_url": f"/register/verify?token={token}",
        }


@router.post("/admin/users/block", dependencies=[Depends(require_admin)])
async def admin_block_user(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    email = validate_email(payload.get("email", ""))

    with STATE_LOCK:
        state = load_state_unlocked()
        if email not in state["blocked_emails"]:
            state["blocked_emails"].append(email)
        user = find_user(state, email)
        if user:
            user["status"] = "blocked"
            clear_access_session(user)
            user["updated_at"] = utc_now()
        state["blocked_emails"].sort()
        save_state_unlocked(state)
        return {"status": "blocked", "email": email}


@router.delete("/admin/users/{email}", dependencies=[Depends(require_admin)])
async def admin_delete_user(email: str) -> dict[str, str]:
    normalized = validate_email(email)

    with STATE_LOCK:
        state = load_state_unlocked()
        before = len(state["users"])
        state["users"] = [user for user in state["users"] if user.get("email") != normalized]
        if len(state["users"]) == before:
            raise HTTPException(status_code=404, detail="User not found.")
        save_state_unlocked(state)
        return {"status": "deleted"}


@router.delete("/admin/blocked-emails/{email}", dependencies=[Depends(require_admin)])
async def admin_unblock_email(email: str) -> dict[str, str]:
    normalized = validate_email(email)

    with STATE_LOCK:
        state = load_state_unlocked()
        if normalized not in state["blocked_emails"]:
            raise HTTPException(status_code=404, detail="Blocked email not found.")
        state["blocked_emails"] = [item for item in state["blocked_emails"] if item != normalized]
        user = find_user(state, normalized)
        if user and user.get("status") == "blocked":
            user["status"] = "waitlisted" if user.get("email_verified") else "pending_verification"
            user["updated_at"] = utc_now()
        save_state_unlocked(state)
        return {"status": "unblocked"}


@router.post("/admin/dmca/blocked-videos", dependencies=[Depends(require_admin)])
async def admin_block_video(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with STATE_LOCK:
        state = load_state_unlocked()
        record = block_video_in_state(
            state,
            payload,
            reason=str(payload.get("reason") or "Manually blocked by admin").strip(),
            source="admin_blocklist",
        )
        save_state_unlocked(state)
        return {"status": "blocked", "video": record}


@router.delete("/admin/dmca/blocked-videos/{video_id}", dependencies=[Depends(require_admin)])
async def admin_unblock_video(video_id: str) -> dict[str, str]:
    normalized_id = extract_video_id(video_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="A valid YouTube video ID is required.")

    with STATE_LOCK:
        state = load_state_unlocked()
        before = len(state["blocked_videos"])
        state["blocked_videos"] = [
            item for item in state["blocked_videos"] if item.get("video_id") != normalized_id
        ]
        if len(state["blocked_videos"]) == before:
            raise HTTPException(status_code=404, detail="Blocked video not found.")
        save_state_unlocked(state)
        return {"status": "unblocked"}

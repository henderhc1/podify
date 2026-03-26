from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from podify.auth import describe_access_state, require_active_user
from podify.config import (
    DISCLAIMER_COPY,
    STATIC_DIR,
    get_max_active_users,
    is_demo_verification_enabled,
    is_email_verification_required,
)
from podify.services.users import active_user_count, validate_email
from podify.services.videos import (
    block_video_in_state,
    extract_video_id,
    is_blocked_video,
    library_stats,
    normalize_video_record,
    resolve_playback_info,
    sanitize_video_payload,
    search_youtube,
)
from podify.state import STATE_LOCK, load_state, load_state_unlocked, save_state_unlocked

router = APIRouter()


@router.get("/")
async def read_root() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    state = load_state()
    return {
        "max_active_users": get_max_active_users(),
        "active_user_count": active_user_count(state),
        "registration_open": active_user_count(state) < get_max_active_users(),
        "disclaimers": DISCLAIMER_COPY,
        "dmca_contact": state["dmca_contact"],
        "library": library_stats(state),
        "security": {
            "demo_verification_enabled": is_demo_verification_enabled(),
            "email_verification_required": is_email_verification_required(),
        },
        "access": describe_access_state(state, request),
    }


@router.get("/search")
async def search(q: str, _: dict[str, Any] = Depends(require_active_user)) -> list[dict[str, Any]]:
    state = load_state()
    blocked_ids = {item.get("video_id") for item in state["blocked_videos"]}
    return await run_in_threadpool(search_youtube, q, blocked_ids)


@router.get("/playback/{video_id}")
async def get_playback(
    video_id: str,
    _: dict[str, Any] = Depends(require_active_user),
) -> dict[str, Any]:
    normalized_id = extract_video_id(video_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="A valid YouTube video ID is required.")

    state = load_state()
    if is_blocked_video(state, normalized_id):
        raise HTTPException(
            status_code=451,
            detail="This video has been blocked from preview after a DMCA notice.",
        )
    return await run_in_threadpool(resolve_playback_info, normalized_id)


@router.get("/library")
async def get_library(_: dict[str, Any] = Depends(require_active_user)) -> list[dict[str, Any]]:
    state = load_state()
    blocked_ids = {item.get("video_id") for item in state["blocked_videos"]}
    items = [
        normalize_video_record(item)
        for item in state["library_items"]
        if item.get("video_id") not in blocked_ids
    ]
    return sorted(items, key=lambda item: item.get("added_at", ""), reverse=True)


@router.post("/library")
async def add_to_library(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(require_active_user),
) -> dict[str, Any]:
    with STATE_LOCK:
        state = load_state_unlocked()
        item = sanitize_video_payload(payload)
        if is_blocked_video(state, item["video_id"]):
            raise HTTPException(
                status_code=451,
                detail="This video has been blocked from preview after a DMCA notice.",
            )

        existing = next(
            (video for video in state["library_items"] if video.get("video_id") == item["video_id"]),
            None,
        )
        if existing:
            return {"status": "exists", "item": existing}

        state["library_items"].insert(0, item)
        save_state_unlocked(state)
        return {"status": "added", "item": item}


@router.delete("/library/{video_id}")
async def remove_from_library(
    video_id: str,
    _: dict[str, Any] = Depends(require_active_user),
) -> dict[str, str]:
    normalized_id = extract_video_id(video_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="A valid YouTube video ID is required.")

    with STATE_LOCK:
        state = load_state_unlocked()
        before = len(state["library_items"])
        state["library_items"] = [
            item for item in state["library_items"] if item.get("video_id") != normalized_id
        ]
        if len(state["library_items"]) == before:
            raise HTTPException(status_code=404, detail="Library item not found.")
        save_state_unlocked(state)
        return {"status": "deleted"}


@router.get("/dmca")
async def get_dmca_info() -> dict[str, Any]:
    state = load_state()
    return {
        "contact": state["dmca_contact"],
        "blocked_videos": state["blocked_videos"],
        "notice_count": len(state["dmca_notices"]),
    }


@router.post("/dmca/notices")
async def submit_dmca_notice(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    reporter_name = str(payload.get("reporter_name") or "").strip()
    reporter_email = validate_email(payload.get("reporter_email", ""))
    video_url = str(payload.get("video_url") or "").strip()
    work_description = str(payload.get("work_description") or "").strip()
    statement = str(payload.get("statement") or "").strip()

    if not reporter_name or not video_url or not work_description or not statement:
        raise HTTPException(
            status_code=422,
            detail="Reporter name, reporter email, video URL, work description, and statement are required.",
        )

    with STATE_LOCK:
        state = load_state_unlocked()
        block_record = block_video_in_state(
            state,
            {
                "video_url": video_url,
                "title": payload.get("title") or "Blocked pending review",
                "description": payload.get("description") or work_description,
            },
            reason="DMCA notice received",
            source="dmca_notice",
        )
        notice = {
            "id": f"notice-{len(state['dmca_notices']) + 1}",
            "reporter_name": reporter_name,
            "reporter_email": reporter_email,
            "video_id": block_record["video_id"],
            "video_url": block_record["video_url"],
            "work_description": work_description,
            "statement": statement,
            "submitted_at": block_record["blocked_at"],
        }
        state["dmca_notices"].insert(0, notice)
        save_state_unlocked(state)

    return {
        "status": "received",
        "message": (
            "Notice received. The identified video has been blocked from preview while the "
            "request is reviewed."
        ),
        "notice_id": notice["id"],
    }

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

APP_TITLE = "Podify"
APP_DESCRIPTION = "A non-commercial YouTube discovery, preview, and attribution tool."

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
DOWNLOADS_DIR = ROOT_DIR / "downloads"
SEARCH_TIMEOUT_SECONDS = 20

DISCLAIMER_COPY = {
    "public_message": (
        "Podify is a non-commercial discovery tool limited to 1,000 users so it stays "
        "small, educational, and focused on driving viewers back to YouTube."
    ),
    "footer": (
        "Podify is a non-commercial discovery tool. All content belongs to its original "
        "creators. Full viewing is intended to occur on YouTube with ads. This tool does "
        "not store video files or redistribute content. Podify is not affiliated with "
        "YouTube or Google."
    ),
    "registration": (
        "By registering, you agree to use Podify only for discovery and preview purposes. "
        "Support creators by watching full content on YouTube. Podify is limited to 1,000 "
        "users and operates as a non-commercial educational prototype."
    ),
    "player": "This is a preview. Support the creator by watching the full video on YouTube.",
}


def load_local_settings() -> dict[str, Any]:
    try:
        module = importlib.import_module("podify.local_settings")
    except ImportError:
        return {}

    return {
        name: getattr(module, name)
        for name in dir(module)
        if name.startswith("PODIFY_")
    }


LOCAL_SETTINGS = load_local_settings()


def get_bool_setting(name: str, default: bool = False) -> bool:
    raw_value = get_setting(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def get_setting(name: str, default: str | None = None) -> str | None:
    env_value = os.getenv(name)
    if env_value not in (None, ""):
        return env_value

    local_value = LOCAL_SETTINGS.get(name)
    if local_value in (None, ""):
        return default
    return str(local_value)


def get_state_path() -> Path:
    configured = get_setting("PODIFY_STATE_PATH")
    return Path(configured) if configured else DATA_DIR / "state.json"


def get_max_active_users() -> int:
    raw_limit = get_setting("PODIFY_MAX_ACTIVE_USERS", "1000") or "1000"
    try:
        return max(1, int(raw_limit))
    except ValueError:
        return 1000


def get_admin_token() -> str:
    return (
        get_setting("PODIFY_ADMIN_TOKEN")
        or get_setting("PODIFY_ADMIN_PASSWORD")
        or ""
    ).strip()


def is_email_verification_required() -> bool:
    return get_bool_setting("PODIFY_REQUIRE_EMAIL_VERIFICATION", default=False)


def is_demo_verification_enabled() -> bool:
    return get_bool_setting("PODIFY_EXPOSE_DEMO_VERIFICATION", default=False)


def get_dmca_contact_defaults() -> dict[str, Any]:
    raw_window = get_setting("PODIFY_DMCA_RESPONSE_WINDOW_HOURS", "48") or "48"
    try:
        response_window_hours = max(1, int(raw_window))
    except ValueError:
        response_window_hours = 48

    return {
        "agent_name": get_setting("PODIFY_DMCA_AGENT_NAME", "Pending registration")
        or "Pending registration",
        "agent_email": get_setting("PODIFY_DMCA_AGENT_EMAIL", "dmca@podify.com")
        or "dmca@podify.com",
        "response_window_hours": response_window_hours,
    }


def build_default_state_template() -> dict[str, Any]:
    return {
        "library_items": [],
        "users": [],
        "blocked_emails": [],
        "blocked_videos": [],
        "dmca_contact": get_dmca_contact_defaults(),
        "dmca_notices": [],
    }

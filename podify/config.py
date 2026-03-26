from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any

APP_TITLE = "Podify"
APP_DESCRIPTION = "A non-commercial YouTube discovery, preview, and attribution tool."

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
DOWNLOADS_DIR = ROOT_DIR / "downloads"
SEARCH_TIMEOUT_SECONDS = 20
YTDLP_ENV_COOKIE_FILE = DATA_DIR / "yt-dlp-cookies.txt"
YTDLP_RUNTIME_COOKIE_FILE = DATA_DIR / "yt-dlp-cookies.runtime.txt"

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
COOKIE_BROWSER_SETTING_RE = re.compile(
    r"""(?x)
    (?P<name>[^+:]+)
    (?:\s*\+\s*(?P<keyring>[^:]+))?
    (?:\s*:\s*(?!:)(?P<profile>.+?))?
    (?:\s*::\s*(?P<container>.+))?
    """
)


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


def _write_text_if_changed(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                return str(path)
        except OSError:
            pass
    path.write_text(content, encoding="utf-8")
    return str(path)


def _normalize_cookie_text(content: str) -> str:
    return f"{str(content or '').replace('\r\n', '\n').strip()}\n"


def _validate_runtime_cookie_text(cookie_text: str) -> str:
    normalized = _normalize_cookie_text(cookie_text).strip()
    if not normalized:
        raise ValueError("Cookie text is required.")

    lines = [line for line in normalized.split("\n") if line.strip()]
    data_lines = [line for line in lines if not line.lstrip().startswith("#")]
    if not data_lines:
        raise ValueError("Cookie text must include at least one cookie row.")

    if not any(len(line.split("\t")) >= 7 for line in data_lines):
        raise ValueError("Cookie text must be in Netscape cookies.txt format.")

    return f"{normalized}\n"


def _resolve_ytdlp_env_cookie_file() -> str | None:
    direct_path = get_setting("PODIFY_YTDLP_COOKIE_FILE") or get_setting("PODIFY_YTDLP_COOKIEFILE")
    if direct_path:
        return direct_path

    cookie_text = get_setting("PODIFY_YTDLP_COOKIE_TEXT") or get_setting("PODIFY_YTDLP_COOKIES")
    if not cookie_text:
        return None
    return _write_text_if_changed(YTDLP_ENV_COOKIE_FILE, _normalize_cookie_text(cookie_text))


def get_ytdlp_runtime_cookie_file_path() -> Path:
    return YTDLP_RUNTIME_COOKIE_FILE


def save_ytdlp_runtime_cookie_text(cookie_text: str) -> str:
    return _write_text_if_changed(YTDLP_RUNTIME_COOKIE_FILE, _validate_runtime_cookie_text(cookie_text))


def clear_ytdlp_runtime_cookie_file() -> bool:
    runtime_path = get_ytdlp_runtime_cookie_file_path()
    if not runtime_path.exists():
        return False
    runtime_path.unlink()
    return True


def get_ytdlp_cookie_file() -> str | None:
    env_cookie_file = _resolve_ytdlp_env_cookie_file()
    if env_cookie_file:
        return env_cookie_file

    runtime_path = get_ytdlp_runtime_cookie_file_path()
    if runtime_path.exists():
        return str(runtime_path)
    return None


def get_ytdlp_cookies_from_browser() -> tuple[str, str | None, str | None, str | None] | None:
    raw_value = (
        get_setting("PODIFY_YTDLP_COOKIES_FROM_BROWSER")
        or get_setting("PODIFY_YTDLP_COOKIE_BROWSER")
        or ""
    ).strip()
    if not raw_value:
        return None

    match = COOKIE_BROWSER_SETTING_RE.fullmatch(raw_value)
    if not match:
        return None

    browser_name = str(match.group("name") or "").strip().lower()
    if not browser_name:
        return None

    profile = str(match.group("profile") or "").strip() or None
    keyring = str(match.group("keyring") or "").strip().upper() or None
    container = str(match.group("container") or "").strip() or None
    return (browser_name, profile, keyring, container)


def _format_browser_cookie_source(
    browser: tuple[str, str | None, str | None, str | None],
) -> str:
    browser_name, profile, keyring, container = browser
    value = browser_name
    if keyring:
        value = f"{value}+{keyring}"
    if profile:
        value = f"{value}:{profile}"
    if container:
        value = f"{value}::{container}"
    return value


def get_ytdlp_cookie_status() -> dict[str, Any]:
    runtime_path = get_ytdlp_runtime_cookie_file_path()
    env_direct_path = get_setting("PODIFY_YTDLP_COOKIE_FILE") or get_setting("PODIFY_YTDLP_COOKIEFILE")
    env_cookie_text = get_setting("PODIFY_YTDLP_COOKIE_TEXT") or get_setting("PODIFY_YTDLP_COOKIES")
    browser_cookies = get_ytdlp_cookies_from_browser()
    active_cookie_file = get_ytdlp_cookie_file()

    if env_direct_path:
        active_source = "env_file"
    elif env_cookie_text:
        active_source = "env_text"
    elif runtime_path.exists():
        active_source = "runtime_file"
    elif browser_cookies:
        active_source = "browser"
    else:
        active_source = "none"

    return {
        "configured": active_source != "none",
        "active_source": active_source,
        "active_cookie_file": active_cookie_file,
        "active_browser": _format_browser_cookie_source(browser_cookies) if browser_cookies else "",
        "runtime_cookie_file": str(runtime_path),
        "runtime_cookie_present": runtime_path.exists(),
    }


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

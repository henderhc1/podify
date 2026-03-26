from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from podify.config import build_default_state_template, get_state_path

STATE_LOCK = Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clone_default_state() -> dict[str, Any]:
    return deepcopy(build_default_state_template())


def merge_defaults(existing: Any, template: Any) -> Any:
    if isinstance(template, dict):
        current = existing if isinstance(existing, dict) else {}
        merged: dict[str, Any] = {}
        for key, value in template.items():
            merged[key] = merge_defaults(current.get(key), value)
        for key, value in current.items():
            if key not in merged:
                merged[key] = value
        return merged
    if isinstance(template, list):
        return existing if isinstance(existing, list) else deepcopy(template)
    return deepcopy(template) if existing is None else existing


def write_state_unlocked(state: dict[str, Any]) -> None:
    get_state_path().write_text(f"{json.dumps(state, indent=2)}\n", encoding="utf-8")


def ensure_state_file_unlocked() -> None:
    state_path = get_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        write_state_unlocked(clone_default_state())
        return

    try:
        raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raw_state = {}

    normalized = merge_defaults(raw_state, clone_default_state())
    if normalized != raw_state:
        write_state_unlocked(normalized)


def load_state_unlocked() -> dict[str, Any]:
    ensure_state_file_unlocked()
    return merge_defaults(
        json.loads(get_state_path().read_text(encoding="utf-8")),
        clone_default_state(),
    )


def save_state_unlocked(state: dict[str, Any]) -> None:
    write_state_unlocked(state)


def load_state() -> dict[str, Any]:
    with STATE_LOCK:
        return load_state_unlocked()


def save_state(state: dict[str, Any]) -> None:
    with STATE_LOCK:
        ensure_state_file_unlocked()
        save_state_unlocked(state)

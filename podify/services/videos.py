from __future__ import annotations

import concurrent.futures
import re
import time
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from fastapi import HTTPException

from podify.config import SEARCH_TIMEOUT_SECONDS, get_ytdlp_cookie_file
from podify.state import utc_now

YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:watch\?.*?v=|embed/|shorts/|live/))([A-Za-z0-9_-]{11})"
)
RAW_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ALLOWED_YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
PLAYBACK_CACHE_TTL_SECONDS = 300
PLAYBACK_CACHE: dict[str, dict[str, Any]] = {}
PLAYBACK_CACHE_LOCK = Lock()
YTDLP_BOT_CHECK_MARKERS = (
    "sign in to confirm you're not a bot",
    "use --cookies-from-browser or --cookies",
)


def normalize_candidate_url(candidate: str) -> str:
    if "://" in candidate:
        return candidate

    if candidate.startswith(
        (
            "youtu.be/",
            "youtube.com/",
            "www.youtube.com/",
            "m.youtube.com/",
            "music.youtube.com/",
            "youtube-nocookie.com/",
            "www.youtube-nocookie.com/",
        )
    ):
        return f"https://{candidate}"

    return candidate


def normalize_host(netloc: str) -> str:
    return netloc.split("@")[-1].split(":")[0].lower()


def is_allowed_youtube_url(value: str) -> bool:
    candidate = normalize_candidate_url(str(value or "").strip())
    parsed = urlparse(candidate)
    return parsed.scheme in {"http", "https"} and normalize_host(parsed.netloc) in ALLOWED_YOUTUBE_HOSTS


def extract_video_id(value: str) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if RAW_YOUTUBE_ID_RE.fullmatch(candidate):
        return candidate

    normalized_candidate = normalize_candidate_url(candidate)
    parsed = urlparse(normalized_candidate)
    host = normalize_host(parsed.netloc)
    video_id: str | None = None

    if parsed.scheme in {"http", "https"} and host in ALLOWED_YOUTUBE_HOSTS:
        path_parts = [part for part in parsed.path.split("/") if part]
        if host == "youtu.be":
            video_id = path_parts[0] if path_parts else None
        elif path_parts and path_parts[0] in {"embed", "shorts", "live"}:
            video_id = path_parts[1] if len(path_parts) > 1 else None
        else:
            video_id = parse_qs(parsed.query).get("v", [None])[0]

    if video_id and RAW_YOUTUBE_ID_RE.fullmatch(video_id):
        return video_id

    match = YOUTUBE_ID_RE.fullmatch(candidate)
    if match:
        return match.group(1)
    return None


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "Live"
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def summarize_description(text: str, limit: int = 220) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return "Preview this video, then continue on YouTube to support the creator."
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 3].rstrip()}..."


def canonical_watch_url(video_id: str, fallback_url: str | None = None) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def build_playback_api_url(video_id: str) -> str:
    return f"/playback/{video_id}"


def build_embed_url(video_id: str) -> str:
    return f"https://www.youtube.com/embed/{video_id}"


def library_stats(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "saved_videos": len(state["library_items"]),
        "blocked_videos": len(state["blocked_videos"]),
    }


def is_blocked_video(state: dict[str, Any], video_id: str) -> bool:
    return any(item.get("video_id") == video_id for item in state["blocked_videos"])


def sanitize_video_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source_url = str(
        payload.get("video_url") or payload.get("watch_url") or payload.get("video_id") or ""
    ).strip()
    video_id = extract_video_id(source_url)
    if not video_id:
        raise HTTPException(status_code=422, detail="A valid YouTube video URL or ID is required.")

    title = str(payload.get("title") or "Untitled video").strip()
    channel = str(payload.get("channel") or payload.get("uploader") or "Unknown creator").strip()
    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "duration": str(payload.get("duration") or "").strip() or "Unknown",
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "video_url": canonical_watch_url(video_id),
        "playback_url": build_playback_api_url(video_id),
        "embed_url": build_embed_url(video_id),
        "description": summarize_description(str(payload.get("description") or "")),
        "added_at": payload.get("added_at") or utc_now(),
    }


def normalize_video_record(payload: dict[str, Any]) -> dict[str, Any]:
    video_id = extract_video_id(
        str(payload.get("video_id") or payload.get("video_url") or payload.get("watch_url") or "")
    )
    if not video_id:
        return dict(payload)

    normalized = dict(payload)
    normalized["video_id"] = video_id
    normalized["thumbnail_url"] = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    normalized["video_url"] = canonical_watch_url(video_id)
    normalized["playback_url"] = build_playback_api_url(video_id)
    normalized["embed_url"] = build_embed_url(video_id)
    return normalized


def build_search_result(entry: dict[str, Any]) -> dict[str, Any] | None:
    video_id = (
        entry.get("id")
        or extract_video_id(entry.get("webpage_url", ""))
        or extract_video_id(entry.get("url", ""))
    )
    if not video_id:
        return None

    raw_duration = entry.get("duration")
    live_status = str(entry.get("live_status") or "").lower()
    if isinstance(raw_duration, (int, float)) and raw_duration > 0:
        duration = format_duration(raw_duration)
    elif live_status in {"is_live", "was_live"}:
        duration = "Live"
    else:
        duration = str(entry.get("duration_string") or "").strip() or "Unknown"

    return {
        "video_id": video_id,
        "title": str(entry.get("title") or "Untitled video").strip(),
        "channel": str(entry.get("uploader") or entry.get("channel") or "Unknown creator").strip(),
        "duration": duration,
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "video_url": canonical_watch_url(video_id),
        "playback_url": build_playback_api_url(video_id),
        "embed_url": build_embed_url(video_id),
        "description": summarize_description(
            str(entry.get("description") or entry.get("alt_title") or "")
        ),
    }


def block_video_in_state(
    state: dict[str, Any],
    payload: dict[str, Any],
    reason: str,
    source: str,
) -> dict[str, Any]:
    video = sanitize_video_payload(payload)
    existing = next(
        (item for item in state["blocked_videos"] if item.get("video_id") == video["video_id"]),
        None,
    )
    if existing:
        existing.update(
            {
                "reason": reason,
                "source": source,
                "blocked_at": existing.get("blocked_at") or utc_now(),
                "title": video["title"],
                "video_url": video["video_url"],
            }
        )
        block_record = existing
    else:
        block_record = {
            **video,
            "reason": reason,
            "source": source,
            "blocked_at": utc_now(),
        }
        state["blocked_videos"].insert(0, block_record)

    state["library_items"] = [
        item for item in state["library_items"] if item.get("video_id") != video["video_id"]
    ]
    return block_record


def mime_type_for_extension(ext: str | None) -> str | None:
    normalized = str(ext or "").lower()
    if normalized == "mp4":
        return "video/mp4"
    if normalized == "webm":
        return "video/webm"
    if normalized == "m3u8":
        return "application/vnd.apple.mpegurl"
    return None


def describe_source_quality(fmt: dict[str, Any]) -> str:
    label = str(fmt.get("format_note") or fmt.get("resolution") or "").strip()
    if label:
        return label

    height = int(fmt.get("height") or 0)
    if height > 0:
        return f"{height}p"
    return ""


def browser_playback_sort_key(fmt: dict[str, Any]) -> tuple[int, int, int, float]:
    ext = str(fmt.get("ext") or "").lower()
    protocol = str(fmt.get("protocol") or "").lower()
    return (
        1 if ext == "mp4" else 0,
        1 if ext == "webm" else 0,
        int(fmt.get("height") or 0),
        float(fmt.get("tbr") or 0.0) + (0.1 if protocol in {"https", "http"} else 0.0),
    )


def is_browser_playable_combined_format(fmt: dict[str, Any]) -> bool:
    url = str(fmt.get("url") or "").strip()
    if not url:
        return False

    ext = str(fmt.get("ext") or "").lower()
    protocol = str(fmt.get("protocol") or "").lower()
    if fmt.get("acodec") in (None, "none") or fmt.get("vcodec") in (None, "none"):
        return False
    if any(token in protocol for token in ("dash", "ism")):
        return False

    return ext in {"mp4", "webm", "m3u8"} or protocol in {"https", "http", "m3u8", "m3u8_native"}


def select_browser_playback_sources(info: dict[str, Any]) -> list[dict[str, Any]]:
    formats = info.get("formats") or []
    candidates = [fmt for fmt in formats if is_browser_playable_combined_format(fmt)]
    ordered = sorted(candidates, key=browser_playback_sort_key, reverse=True)

    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for fmt in ordered:
        url = str(fmt.get("url") or "").strip()
        if not url or url in seen_urls:
            continue

        sources.append(
            {
                "url": url,
                "mime_type": mime_type_for_extension(fmt.get("ext")),
                "format_id": str(fmt.get("format_id") or ""),
                "quality": describe_source_quality(fmt),
            }
        )
        seen_urls.add(url)
        if len(sources) >= 3:
            break

    return sources


def get_cached_playback_info(video_id: str) -> dict[str, Any] | None:
    now = time.time()
    with PLAYBACK_CACHE_LOCK:
        cached = PLAYBACK_CACHE.get(video_id)
        if not cached:
            return None
        if cached["expires_at"] <= now:
            PLAYBACK_CACHE.pop(video_id, None)
            return None
        return dict(cached["payload"])


def cache_playback_info(video_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    expires_at = int(time.time()) + PLAYBACK_CACHE_TTL_SECONDS
    cached_payload = {**payload, "expires_at": expires_at}
    with PLAYBACK_CACHE_LOCK:
        PLAYBACK_CACHE[video_id] = {
            "expires_at": expires_at,
            "payload": cached_payload,
        }
    return dict(cached_payload)


def build_ydl_options(*, flat_search: bool = False) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "geo_bypass": True,
        "extract_flat": "in_playlist" if flat_search else False,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        },
    }
    if flat_search:
        options["lazy_playlist"] = True

    cookie_file = get_ytdlp_cookie_file()
    if cookie_file:
        options["cookiefile"] = cookie_file
    return options


def is_ytdlp_bot_check_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(marker in message for marker in YTDLP_BOT_CHECK_MARKERS)


def ytdlp_operator_guidance(context: str) -> str:
    if get_ytdlp_cookie_file():
        return (
            f"{context} YouTube is still challenging this server even with cookies configured. "
            "Refresh the yt-dlp cookies and try again."
        )
    return (
        f"{context} YouTube is challenging this server IP. Configure "
        "PODIFY_YTDLP_COOKIE_FILE or PODIFY_YTDLP_COOKIE_TEXT so yt-dlp can use authenticated "
        "YouTube cookies on the server."
    )


def resolve_playback_info(video_id_or_url: str) -> dict[str, Any]:
    video_id = extract_video_id(video_id_or_url)
    if not video_id:
        raise HTTPException(status_code=422, detail="A valid YouTube video URL or ID is required.")

    cached = get_cached_playback_info(video_id)
    if cached:
        return cached

    ydl_opts = build_ydl_options(flat_search=False)

    def run_lookup() -> dict[str, Any]:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(canonical_watch_url(video_id), download=False)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_lookup)
            info = future.result(timeout=SEARCH_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Playback lookup timed out. Please try again.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        if is_ytdlp_bot_check_error(exc):
            raise HTTPException(
                status_code=503,
                detail=ytdlp_operator_guidance("Preview lookup is temporarily unavailable."),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail="Playback lookup failed. Please try another video or use Watch on YouTube.",
        ) from exc

    if not info or info.get("_type") == "playlist":
        raise HTTPException(status_code=404, detail="Playable video details were not found.")

    sources = select_browser_playback_sources(info)
    if not sources:
        raise HTTPException(
            status_code=422,
            detail="No browser-playable preview stream was found for this video.",
        )

    payload = {
        "video_id": video_id,
        "title": str(info.get("title") or "Untitled video").strip(),
        "channel": str(info.get("uploader") or info.get("channel") or "Unknown creator").strip(),
        "duration": format_duration(info.get("duration")),
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "video_url": canonical_watch_url(video_id),
        "playback_url": build_playback_api_url(video_id),
        "description": summarize_description(str(info.get("description") or "")),
        "stream_url": sources[0]["url"],
        "mime_type": sources[0]["mime_type"],
        "sources": sources,
    }
    return cache_playback_info(video_id, payload)


def search_youtube(query: str, blocked_ids: set[str] | None = None) -> list[dict[str, Any]]:
    cleaned_query = str(query or "").strip()
    if not cleaned_query:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required.")
    if len(cleaned_query) > 200:
        raise HTTPException(status_code=422, detail="Search queries must be 200 characters or fewer.")

    ignored_ids = blocked_ids or set()
    is_direct_url = cleaned_query.startswith("http://") or cleaned_query.startswith("https://")
    ydl_opts = build_ydl_options(flat_search=not is_direct_url)
    ydl_opts["default_search"] = "ytsearch"

    def run_lookup() -> list[dict[str, Any]]:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if is_direct_url:
                if not is_allowed_youtube_url(cleaned_query):
                    raise HTTPException(
                        status_code=422,
                        detail="Only direct YouTube URLs are allowed.",
                    )
                info = ydl.extract_info(cleaned_query, download=False)
                if not info:
                    return []
                if info.get("_type") == "playlist":
                    return info.get("entries", []) or []
                return [info]
            result = ydl.extract_info(f"ytsearch10:{cleaned_query}", download=False)
            return result.get("entries", []) or []

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_lookup)
            entries = future.result(timeout=SEARCH_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Search timed out. Please try again.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        if is_ytdlp_bot_check_error(exc):
            raise HTTPException(
                status_code=503,
                detail=ytdlp_operator_guidance("Search is temporarily unavailable."),
            ) from exc
        raise HTTPException(status_code=500, detail="Search failed. Please try again.") from exc

    results: list[dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        video = build_search_result(entry)
        if not video or video["video_id"] in ignored_ids:
            continue
        results.append(video)

    return results[:10]

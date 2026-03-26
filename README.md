# Podify

Podify is a non-commercial YouTube discovery and preview tool. Users can request access with an email address, sign in through the access flow, preview videos inside a custom interface, save them to a lightweight library, and then continue to YouTube through clear attribution links.

## Product Direction

- Search returns up to 10 YouTube results per query.
- Preview playback is resolved on demand with `yt-dlp` and rendered in Podify's existing HTML5 player UI.
- Search, playback, and library access are gated behind active-user sessions.
- Every result and saved item includes `Watch on YouTube - Support the Creator`.
- Registration supports immediate valid-email signup by default, optional email verification mode, waitlisting, and a hard active-user cap.
- Admin controls support adding, approving, deleting, and blocking users by email.
- DMCA notice handling blocks videos from preview and removes them from the saved library.
- Legal disclaimers are shown in the UI to keep the product framed as discovery and preview.

This repository is set up so most of the project remains open, while local state, secrets, and operator-specific settings stay untracked.

## Project Layout

```text
main.py                  Compatibility entrypoint for local runs and Railway (`main:app`)
podify/app.py            FastAPI app wiring
podify/routes/           Public, access, and admin route modules
podify/services/         Search, video, and user helper functions
podify/state.py          JSON state loading/saving
podify/config.py         Environment and local-settings helpers
static/index.html        Single-page frontend
data/                    Local JSON state directory (ignored except for .gitkeep)
downloads/               Local scratch/download area (ignored except for .gitkeep)
```

## Preview Playback

Podify keeps the current UI, but preview playback is resolved through `yt-dlp` instead of a YouTube iframe. When a user opens a preview, the backend resolves browser-playable source URLs for that specific YouTube video and returns them to the frontend player. Podify does not permanently store downloaded video files; playback URLs are resolved on demand and cached briefly in memory.
Podify does not use the YouTube Data API for search or playback resolution.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://localhost:8000`.

## Configuration

Podify reads configuration from environment variables first. If you want a private local-only Python config file, copy `podify/local_settings.py.example` to `podify/local_settings.py`. That file is intentionally ignored by Git.

Supported settings:

```powershell
$env:PODIFY_MAX_ACTIVE_USERS="1000"
$env:PODIFY_ADMIN_TOKEN="replace-me"
$env:PODIFY_REQUIRE_EMAIL_VERIFICATION="0"
$env:PODIFY_EXPOSE_DEMO_VERIFICATION="0"
$env:PODIFY_YTDLP_COOKIE_FILE="data/yt-dlp-cookies.txt"
$env:PODIFY_YTDLP_COOKIES_FROM_BROWSER="chrome:Default"
$env:PODIFY_YTDLP_MAX_CONCURRENT_LOOKUPS="1"
$env:PODIFY_YTDLP_PROXY="http://user:pass@proxy-host:port"
$env:PODIFY_YTDLP_SOURCE_ADDRESS="203.0.113.10"
$env:PODIFY_YTDLP_SLEEP_REQUESTS_SECONDS="0.25"
$env:PODIFY_YTDLP_BOTCHECK_RETRY_SLEEP_REQUESTS_SECONDS="1.0"
$env:PODIFY_DMCA_AGENT_NAME="Your DMCA Agent"
$env:PODIFY_DMCA_AGENT_EMAIL="dmca@example.com"
$env:PODIFY_DMCA_RESPONSE_WINDOW_HOURS="48"
$env:PODIFY_STATE_PATH="data/state.json"
```

Set `PODIFY_ADMIN_TOKEN` before using the admin API locally. Admin routes stay disabled until that token is configured.
`PODIFY_REQUIRE_EMAIL_VERIFICATION` is disabled by default right now, so valid emails can sign up immediately. Turn it on later when a real outbound email flow is ready.
`PODIFY_EXPOSE_DEMO_VERIFICATION` only matters when email verification is enabled. Leave it off for secure behavior; only turn it on for local demo testing.
`PODIFY_YTDLP_COOKIE_FILE`, `PODIFY_YTDLP_COOKIE_TEXT`, or `PODIFY_YTDLP_COOKIES_FROM_BROWSER` is optional if you choose to provide authenticated YouTube cookies.
`PODIFY_YTDLP_MAX_CONCURRENT_LOOKUPS` controls the shared yt-dlp worker pool size (default `1` and recommended for Railway).
`PODIFY_YTDLP_PROXY` and `PODIFY_YTDLP_SOURCE_ADDRESS` let you force yt-dlp egress through a different outbound path.
`PODIFY_YTDLP_SLEEP_REQUESTS_SECONDS` controls pause time between yt-dlp requests (default `0.25`).
`PODIFY_YTDLP_BOTCHECK_RETRY_SLEEP_REQUESTS_SECONDS` controls the stricter retry profile pause after bot-check errors (default `1.0`).
Admins can also upload Netscape `cookies.txt` content directly from the Admin UI (`/admin/ytdlp/cookies`), which Podify stores as `data/yt-dlp-cookies.runtime.txt` and uses automatically when env cookie settings are not present.

## Access Control

Podify's public landing page and DMCA flow stay visible, but the service itself is not open access anymore:

- `POST /register` starts the access flow for a given email.
- With the default config, valid emails are accepted immediately and `POST /register` issues the normal HTTP-only access cookie.
- `GET /register/verify?token=...` is still available for the optional verification mode and future real email delivery.
- `GET /search`, `GET /playback/{video_id}`, and all `/library` routes require a signed-in user with `active` status.
- Waitlisted users can sign in and see their state, but they still cannot use the service until promoted to `active`.
- `POST /session/logout` clears the browser session cookie and invalidates the stored session token.

## Testing Users Before Real Email Delivery

Until a real outbound email provider is wired up, testers can sign up directly with valid emails. The admin-issued link flow is still available if you later re-enable verification mode or need to hand a tester a direct sign-in path:

- An admin can add or approve a user as `active`.
- Users can then sign up directly with that email and receive the normal access session.
- If verification mode is enabled later, the admin can still generate a one-time test access link from the admin UI or `POST /admin/users/access-link`.
- That keeps the future verification path testable without opening the service completely.

## Private Files And `.gitignore`

The ignore rules keep these local-only by default:

- `data/` runtime state
- `downloads/` scratch files
- `.env` files
- `podify/local_settings.py`
- temporary test artifacts such as `podify-request-*` and `podify-search-*`

## Security Defaults

- Admin routes are disabled until `PODIFY_ADMIN_TOKEN` is explicitly configured.
- Registration verification tokens are hashed in state when verification mode is enabled.
- Browser access sessions are stored as hashed, server-validated tokens and issued after signup or verification.
- Direct URL lookups only accept YouTube URLs.
- Library, playback, thumbnail, and watch URLs are derived from validated YouTube video IDs instead of trusting client-supplied URLs.
- Security headers are added to responses to reduce framing, sniffing, and cross-origin policy risks.
- Basic per-IP rate limiting is applied to search, playback, registration, DMCA, and admin endpoints to reduce brute-force and abuse.

Important: `.gitignore` prevents accidental commits. It does **not** prevent copying of code that is already committed and public. If you need real protection, use a private repository and an explicit license.

## Railway Notes

- `main.py` remains the stable ASGI entrypoint, so existing `main:app` deployment commands keep working.
- `nixpacks.toml` keeps `ffmpeg` available for platforms that rely on Nixpacks.
- Keep secrets in Railway environment variables instead of committed files.
- Search uses flat `yt-dlp` query extraction to reduce per-result lookups and improve load time.
- If Railway logs show `Sign in to confirm you're not a bot`, set `PODIFY_YTDLP_MAX_CONCURRENT_LOOKUPS=1`, redeploy/restart, and retry.
- Raise `PODIFY_YTDLP_SLEEP_REQUESTS_SECONDS` (for example `0.75` to `1.5`) to slow extraction request bursts.
- If needed, route egress differently with `PODIFY_YTDLP_PROXY` or `PODIFY_YTDLP_SOURCE_ADDRESS` (and/or move regions) so requests come from a different outbound path.
- Cookie-based auth remains optional: `PODIFY_YTDLP_COOKIE_FILE`, `PODIFY_YTDLP_COOKIE_TEXT`, and `PODIFY_YTDLP_COOKIES_FROM_BROWSER` can still be used when your threat model permits it.
- Playback now degrades gracefully when YouTube blocks stream extraction: Podify keeps the modal open, shows the reason, and preserves `Watch on YouTube - Support the Creator`.

## Tests

```powershell
python -m unittest test_search.py test_request.py
```

## Operator Note

The repository includes implementation hooks for attribution, DMCA handling, user caps, and registration gating, but legal compliance is still an operator responsibility. This README is project documentation, not legal advice.

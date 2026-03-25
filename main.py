from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import json
import re
from datetime import datetime
import subprocess
import shutil
import concurrent.futures

app = FastAPI()

AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.webm', '.aac', '.ogg', '.wav', '.flac'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.mov', '.avi', '.m4v', '.webm'}
MEDIA_TYPES = {
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.ogg': 'audio/ogg',
    '.wav': 'audio/wav',
    '.flac': 'audio/flac',
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.webm': 'video/webm',
    '.mkv': 'video/x-matroska',
    '.mov': 'video/quicktime',
    '.avi': 'video/x-msvideo',
}

# CORS
origins = [
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Root route serves index.html
@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

# Ensure downloads folder exists
DOWNLOADS_DIR = "downloads"
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Search endpoint
@app.get("/search")
async def search(q: str):
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    try:
        # First try using the yt-dlp Python API (fast, no external binary needed)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'default_search': 'ytsearch',
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            },
            'geo_bypass': True,
        }
        def run_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch3:{q}", download=False)

        # Prevent hangs by enforcing a timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_search)
            try:
                search_result = future.result(timeout=15)
            except concurrent.futures.TimeoutError:
                raise HTTPException(status_code=504, detail="Search timed out. Please try again.")

        entries = search_result.get('entries', []) or []
        results = []
        for entry in entries:
            if not entry or not entry.get('title'):
                continue
            duration = int(entry.get('duration') or 0)
            minutes = duration // 60
            seconds = duration % 60
            duration_str = f"{minutes:02d}:{seconds:02d}"
            results.append({
                "title": entry.get('title', ''),
                "channel": entry.get('uploader', ''),
                "duration": duration_str,
                "thumbnail_url": entry.get('thumbnail', ''),
                "video_url": entry.get('webpage_url', '')
            })
        return results
    except Exception as api_error:
        # Fallback: use yt-dlp binary via subprocess (more resilient in some environments)
        try:
            ytdlp_path = os.path.join(os.getcwd(), '.venv', 'Scripts', 'yt-dlp.exe')
            if not os.path.exists(ytdlp_path):
                ytdlp_path = 'yt-dlp'

            cmd = [
                ytdlp_path,
                '--dump-json',
                '--no-download',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                '--geo-bypass',
                f'ytsearch3:{q}'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or 'yt-dlp returned non-zero exit code')

            lines = [l for l in result.stdout.splitlines() if l.strip()]
            results = []
            for line in lines:
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                duration = int(data.get('duration', 0) or 0)
                minutes = duration // 60
                seconds = duration % 60
                duration_str = f"{minutes:02d}:{seconds:02d}"
                results.append({
                    "title": data.get('title', ''),
                    "channel": data.get('uploader', ''),
                    "duration": duration_str,
                    "thumbnail_url": data.get('thumbnail', ''),
                    "video_url": data.get('webpage_url', '')
                })
            return results
        except Exception as sub_error:
            # Surface both error messages for easy debugging
            raise HTTPException(
                status_code=500,
                detail=(
                    "Search failed — cannot fetch results. "
                    f"API error: {str(api_error)} | subprocess error: {str(sub_error)}"
                )
            )

# Download endpoint
@app.post("/download")
async def download(data: dict):
    url = data.get('url')
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    try:
        # Get info first to get title
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'unknown')
            sanitized_title = re.sub(r'[^\w\-_\. ]', '_', title)
            
            # Prefer a single-file video+audio format so the browser can play full video
            formats = info.get('formats', [])
            muxed_formats = [
                f for f in formats
                if f.get('format_id')
                and f.get('acodec') != 'none'
                and f.get('vcodec') != 'none'
            ]
            if muxed_formats:
                best_video = max(
                    muxed_formats,
                    key=lambda f: (
                        f.get('height') or 0,
                        f.get('tbr') or 0,
                        f.get('filesize') or f.get('filesize_approx') or 0,
                    ),
                )
                ext = best_video.get('ext', 'mp4')
                filename = f"{sanitized_title}.{ext}"
                filepath = os.path.join(DOWNLOADS_DIR, filename)
                
                if os.path.exists(filepath):
                    return {"status": "ok", "filename": filename}
                
                # Download the best single-file video format to avoid needing ffmpeg merging
                ydl_opts = {
                    'format': best_video['format_id'],
                    'outtmpl': os.path.join(DOWNLOADS_DIR, f"{sanitized_title}.%(ext)s"),
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                return {"status": "ok", "filename": filename}
            else:
                # Fallback to the best progressive format with both video and audio
                ydl_opts = {
                    'format': 'best[acodec!=none][vcodec!=none]/best',
                    'outtmpl': os.path.join(DOWNLOADS_DIR, f"{sanitized_title}.%(ext)s"),
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                # Get the actual downloaded filename
                downloaded_files = [
                    f for f in os.listdir(DOWNLOADS_DIR)
                    if f.startswith(sanitized_title)
                    and os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
                ]
                if downloaded_files:
                    filename = downloaded_files[0]
                else:
                    filename = f"{sanitized_title}.mp4"  # fallback
                
                return {"status": "ok", "filename": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

# Files endpoint
@app.get("/files")
async def get_files():
    media_extensions = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
    files = []
    for f in os.listdir(DOWNLOADS_DIR):
        if any(f.lower().endswith(ext) for ext in media_extensions):
            filepath = os.path.join(DOWNLOADS_DIR, f)
            stat = os.stat(filepath)
            size_mb = round(stat.st_size / (1024 * 1024), 2)
            date_added = datetime.fromtimestamp(stat.st_mtime).isoformat()
            title = os.path.splitext(f)[0]
            ext = os.path.splitext(f)[1].lower()
            files.append({
                "filename": f,
                "title": title,
                "size_mb": size_mb,
                "date_added": date_added,
                "media_type": "video" if ext in VIDEO_EXTENSIONS else "audio",
            })
    return files

# Media streaming
@app.get("/audio/{filename}")
@app.get("/media/{filename}")
async def get_media(filename: str):
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    
    ext = os.path.splitext(filename)[1].lower()
    media_type = MEDIA_TYPES.get(ext, 'application/octet-stream')
    return FileResponse(filepath, media_type=media_type)

# Delete media
@app.delete("/audio/{filename}")
@app.delete("/media/{filename}")
async def delete_media(filename: str):
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(filepath)
    return {"status": "deleted"}

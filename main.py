from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import re
from datetime import datetime
import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def read_root():
    return FileResponse("static/index.html")


@app.get("/search")
async def search(q: str):
    if not q:
        raise HTTPException(status_code=400, detail="Query is required")
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'ignoreerrors': True,
            'no_color': True,
            'extract_flat': True,
        }

        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # ytsearch15 returns 15 results; extract_flat keeps it fast.
                return ydl.extract_info(f"ytsearch15:{q}", download=False)

        search_result = await asyncio.to_thread(do_search)

        entries = search_result.get('entries', []) or []
        results = []
        for entry in entries:
            if not entry or not entry.get('title'):
                continue

            duration = entry.get('duration') or 0
            minutes = int(duration) // 60
            seconds = int(duration) % 60

            # If the flat extraction doesn't include a thumbnail, build the standard YouTube thumbnail URL.
            thumbnail = entry.get('thumbnail')
            if not thumbnail and entry.get('id'):
                thumbnail = f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg"

            # Prefer webpage_url but fall back to URL/id for flattened entries.
            video_url = entry.get('webpage_url') or entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id', '')}"

            results.append({
                "title": entry.get('title', ''),
                "channel": entry.get('uploader') or entry.get('channel', ''),
                "duration": f"{minutes}:{seconds:02d}",
                "thumbnail_url": thumbnail or '',
                "video_url": video_url,
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/download")
async def download(data: dict):
    url = data.get('url')
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    try:
        # Get video metadata to build a stable filename and know the format
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True, 'no_color': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'unknown')
            ext = info.get('ext', 'm4a')
            sanitized = re.sub(r'[<>:"/\\|?*]', '_', title).strip()[:100]

        filename = f"{sanitized}.{ext}"
        filepath = os.path.join(DOWNLOADS_DIR, filename)

        if os.path.exists(filepath):
            return {"status": "ok", "filename": filename}

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(DOWNLOADS_DIR, f'{sanitized}.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'no_color': True,
        }

        # Download in a separate thread so the event loop stays responsive
        def do_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        await asyncio.to_thread(do_download)

        return {"status": "ok", "filename": filename}
    except Exception as e:
        err = str(e)
        if 'ffprobe' in err.lower() or 'ffmpeg' in err.lower():
            err = 'FFmpeg/ffprobe not found. Please install ffmpeg and ensure it is on your PATH.'
        raise HTTPException(status_code=500, detail=f"Download failed: {err}")


@app.get("/files")
async def get_files():
    audio_exts = {'.mp3', '.m4a', '.webm', '.aac', '.ogg', '.wav', '.flac'}
    files = []
    for f in sorted(
        os.listdir(DOWNLOADS_DIR),
        key=lambda x: os.path.getmtime(os.path.join(DOWNLOADS_DIR, x)),
        reverse=True
    ):
        if any(f.lower().endswith(ext) for ext in audio_exts):
            fp = os.path.join(DOWNLOADS_DIR, f)
            stat = os.stat(fp)
            files.append({
                "filename": f,
                "title": os.path.splitext(f)[0],
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "date_added": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return files


@app.get("/audio/{filename}")
async def stream_audio(filename: str):
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4', '.webm': 'audio/webm',
        '.aac': 'audio/aac', '.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.wav': 'audio/wav', '.flac': 'audio/flac'
    }
    media_type = media_types.get(ext, 'audio/mpeg')

    def iter_file():
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(iter_file(), media_type=media_type, headers={
        "Accept-Ranges": "bytes",
        "Content-Length": str(os.path.getsize(filepath)),
        "Content-Disposition": f'inline; filename="{filename}"',
    })


@app.delete("/audio/{filename}")
async def delete_audio(filename: str):
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(filepath)
    return {"status": "deleted"}
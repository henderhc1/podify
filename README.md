# Podify - Personal YouTube Podcast Manager

## Setup (Windows)

### 1. Install dependencies
```
pip install -r requirements.txt
```

### 2. Make sure ffmpeg is installed
Download from https://ffmpeg.org/download.html and add it to your PATH.
Or install via winget:
```
winget install ffmpeg
```

Verify installation with:
```
ffmpeg -version
ffprobe -version
```

### 3. Run
```
uvicorn main:app --reload
```

### 4. Open browser
Go to: http://localhost:8000

## Usage
- Search for any YouTube video
- Click "↓ Save" to download as mp3
- Click any track in Your Library to play
- Audio continues when screen is off (lock screen controls appear)
- Use speed button to change playback rate

## Folder structure
```
podify/
  main.py          ← backend
  requirements.txt
  static/
    index.html     ← frontend
  downloads/       ← your mp3s live here (auto-created)
```

# ✂ ClipSplitter — YouTube → Short Clips

Paste any YouTube link, choose a clip length (15s / 30s / 60s / custom), and get
ready-to-post MP4 clips plus a ZIP of everything.

## How it works

1. **Analyze** — fetches title, duration & thumbnail via `yt-dlp` (no download yet)
2. **Split** — downloads the video, then `ffmpeg` cuts it into equal-length clips
3. **Download** — each clip individually, or all at once as a `.zip`

## Requirements

- **Python 3.9+**
- **ffmpeg** (must be on your PATH — includes `ffprobe`)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: download from https://ffmpeg.org/download.html and add `bin` to PATH

## Setup & run

```bash
cd youtube-splitter
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:8000** in your browser.

## Features

- Link validation (youtube.com, youtu.be, Shorts)
- Clip-length presets: 15s, 30s, 60s + custom (1–3600s)
- Live progress bar (download % + split %)
- Shows expected clip count before you start
- Two cutting modes:
  - **Fast** (default): stream copy — very quick, cuts snap to the nearest keyframe
  - **Frame-exact**: re-encodes — slower, but every clip is exactly N seconds
- Per-clip download links + single ZIP download
- Jobs cleaned up via "Split another video"

## API

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/analyze` | `{url}` → title, duration, thumbnail |
| POST | `/api/split` | `{url, seconds, accurate}` → `{job_id}` |
| GET  | `/api/job/{id}` | job status, progress, clip manifest |
| GET  | `/api/download/{id}` | ZIP of all clips |
| GET  | `/api/download/{id}/{n}` | single clip MP4 |
| DELETE | `/api/job/{id}` | discard job & files |

## Project layout

```
youtube-splitter/
├── run.py              # entry point
├── requirements.txt
├── app/
│   ├── main.py         # FastAPI routes + job manager
│   └── splitter.py     # yt-dlp download + ffmpeg splitting
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## Notes

- Only split content you own or have permission to reuse. Respect YouTube's
  Terms of Service and copyright law — this tool is for your own content.
- Very long videos take longer to download/split; the page keeps polling so you
  can leave the tab open.

"""Core logic: YouTube metadata (via API), download, and ffmpeg splitting."""
import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from fastapi import HTTPException

YOUTUBE_RE = re.compile(r"(youtube\.com/(watch|shorts|embed)|youtu\.be/)")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# --- Optional cookies (set YT_COOKIES_B64 on Render) ---
COOKIE_FILE_PATH = None
_cookies_b64 = os.getenv("YT_COOKIES_B64")
if _cookies_b64:
    try:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(base64.b64decode(_cookies_b64))
            COOKIE_FILE_PATH = f.name
    except Exception as e:
        print(f"[warn] Failed to decode YT_COOKIES_B64: {e}")

COOKIE_ARGS = ["--cookies", COOKIE_FILE_PATH] if COOKIE_FILE_PATH else []

# --- yt-dlp flags (download step only) ---
YTDLP_BASE_FLAGS = [
    "--no-playlist",
    "--no-warnings",
    # Point the PO-token provider at Chromium inside the container
    "--extractor-args", "youtubepot-wpc:browser_path=/usr/bin/chromium",
    # Prefer clients that tend to work on datacenter IPs
    "--extractor-args", "youtube:player_client=mweb,tv,web_safari",
    "--user-agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    *COOKIE_ARGS,
]


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not YOUTUBE_RE.search(url):
        raise HTTPException(status_code=400, detail="That doesn't look like a YouTube link.")
    return url


def _extract_video_id(url: str) -> str:
    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0].split("&")[0]
    parsed = urlparse(url)
    if parsed.hostname and "youtube.com" in parsed.hostname:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0] in ("shorts", "embed") and len(parts) > 1:
            return parts[1]
    raise HTTPException(status_code=400, detail="Could not extract video ID from URL.")


def _parse_iso_duration(s: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def analyze(url: str) -> dict:
    """Return metadata using the official YouTube Data API."""
    url = validate_url(url)
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API key not configured on server.")

    video_id = _extract_video_id(url)

    try:
        r = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,contentDetails,statistics",
                "id": video_id,
                "key": YOUTUBE_API_KEY,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=422, detail=f"YouTube API error: {e.response.text[:300]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video info: {e}")

    if not data.get("items"):
        raise HTTPException(status_code=404, detail="Video not found or unavailable.")

    item = data["items"][0]
    snippet = item.get("snippet", {})
    content = item.get("contentDetails", {})
    stats = item.get("statistics", {})

    return {
        "id": item.get("id"),
        "title": snippet.get("title"),
        "duration": _parse_iso_duration(content.get("duration", "")),
        "uploader": snippet.get("channelTitle"),
        "thumbnail": (snippet.get("thumbnails", {}).get("high")
                      or snippet.get("thumbnails", {}).get("default", {})).get("url"),
        "view_count": int(stats.get("viewCount", 0) or 0),
    }


def _run(cmd, progress_cb=None, line_cb=None):
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line_cb and "[download]" in line and "%" in line:
            try:
                pct = float(line.split("%")[0].split()[-1])
                if progress_cb:
                    progress_cb(pct)
            except (ValueError, IndexError):
                pass
        if line_cb:
            line_cb(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd[:3])}...")


def download(url: str, out_dir: Path, progress_cb=None) -> Path:
    """Download best mp4 into out_dir using yt-dlp."""
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / "source.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        *YTDLP_BASE_FLAGS,
        "-o", template,
        url,
    ]
    _run(cmd, progress_cb=progress_cb)
    vids = sorted(out_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not vids:
        raise RuntimeError("Download produced no file.")
    return vids[0]


def split(video: Path, out_dir: Path, seconds: int, accurate: bool, progress_cb=None):
    seconds = max(1, min(seconds, 3600))
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(clips_dir / "clip_%03d.mp4")

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    try:
        total = float(probe.stdout.strip())
    except ValueError:
        total = 0

    cmd = ["ffmpeg", "-y", "-i", str(video), "-f", "segment",
           "-segment_time", str(seconds), "-reset_timestamps", "1"]
    if accurate:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-c", "copy"]
    cmd += [pattern]

    state = {"last": -1}

    def line_cb(line: str) -> None:
        m = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
        if m and total and progress_cb:
            done = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            pct = min(99.0, done / total * 100)
            if int(pct) != state["last"]:
                state["last"] = int(pct)
                progress_cb(pct)

    _run(cmd, line_cb=line_cb)

    clips = sorted(clips_dir.glob("clip_*.mp4"))
    if not clips:
        raise RuntimeError("Splitting produced no clips.")
    return clips

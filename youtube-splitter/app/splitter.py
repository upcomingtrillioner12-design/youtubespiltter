"""Core logic: YouTube metadata, download, and ffmpeg-based splitting."""
import json
import re
import subprocess
from pathlib import Path

from fastapi import HTTPException

YOUTUBE_RE = re.compile(r"(youtube\.com/(watch|shorts|embed)|youtu\.be/)")


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not YOUTUBE_RE.search(url):
        raise HTTPException(status_code=400, detail="That doesn't look like a YouTube link.")
    return url


def _run(cmd: list[str], progress_cb=None, line_cb=None) -> None:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line_cb and "[download]" in line and "%" in line:
            try:
                pct = float(line.split("%")[0].split()[-1])
                progress_cb(pct) if progress_cb else None
            except (ValueError, IndexError):
                pass
        line_cb and line_cb(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd[:3])}...")


def analyze(url: str) -> dict:
    """Return title, duration, thumbnail, uploader without downloading."""
    url = validate_url(url)
    proc = subprocess.run(
        ["yt-dlp", "--dump-single-json", "--no-playlist", url],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=422, detail=f"Could not read video info: {proc.stderr.strip()[-300:]}")
    info = json.loads(proc.stdout)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": info.get("thumbnail"),
        "view_count": info.get("view_count"),
    }


def download(url: str, out_dir: Path, progress_cb=None) -> Path:
    """Download best mp4 mux into out_dir, return the file path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / "source.%(ext)s")
    _run([
        "yt-dlp",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-warnings",
        "-o", template,
        url,
    ], progress_cb=progress_cb)
    vids = sorted(out_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not vids:
        raise RuntimeError("Download produced no file.")
    return vids[0]


def split(video: Path, out_dir: Path, seconds: int, accurate: bool,
          progress_cb=None) -> list[Path]:
    """
    Split `video` into chunks of `seconds`.
    accurate=False -> stream copy (fast, snaps to keyframes).
    accurate=True  -> re-encode (slower, frame-exact boundaries).
    """
    seconds = max(1, min(seconds, 3600))
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(clips_dir / "clip_%03d.mp4")

    # total duration for progress estimation
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
    # rename to human-friendly: clip_001.mp4 stays; add duration in manifest instead
    return clips

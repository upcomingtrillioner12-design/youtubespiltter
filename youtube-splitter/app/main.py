"""YouTube Clip Splitter — FastAPI backend.

Flow:
  1. POST /api/analyze          -> video info (title, duration, thumbnail)
  2. POST /api/split            -> {url, seconds, accurate} -> {job_id}
  3. GET  /api/job/{job_id}     -> live status + clip list
  4. GET  /api/download/{job_id}          -> zip of all clips
  5. GET  /api/download/{job_id}/{idx}    -> single clip
"""
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import splitter

BASE_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = BASE_DIR / "jobs"
STATIC_DIR = BASE_DIR / "static"
WORK_DIR.mkdir(exist_ok=True)

app = FastAPI(title="YouTube Clip Splitter", version="1.0.0")
JOBS: dict[str, dict] = {}

PRESETS = [15, 30, 60]


class SplitRequest(BaseModel):
    url: str
    seconds: int = Field(default=30, ge=1, le=3600)
    accurate: bool = False  # re-encode for exact boundaries (slower)


# ---------------------------------------------------------------- routes

@app.post("/api/analyze")
def api_analyze(body: SplitRequest):
    return splitter.analyze(body.url)


def _worker(job_id: str, url: str, seconds: int, accurate: bool) -> None:
    job = JOBS[job_id]
    job_dir = WORK_DIR / job_id
    try:
        job.update(status="downloading", progress=0, message="Downloading video…")
        video = splitter.download(url, job_dir, progress_cb=lambda p: job.update(progress=p))

        job.update(status="splitting", progress=0, message=f"Cutting into {seconds}s clips…")
        clips = splitter.split(video, job_dir, seconds, accurate,
                               progress_cb=lambda p: job.update(progress=p))

        # probe each clip's real duration for the manifest
        manifest = []
        for i, c in enumerate(clips, 1):
            probe = subprocess_run_ffprobe(c)
            manifest.append({
                "index": i,
                "name": c.name,
                "duration": probe,
                "size": c.stat().st_size,
            })

        job.update(status="done", progress=100, message="Ready",
                   clips=manifest, video_title=job.get("video_title", "video"))
    except Exception as e:  # noqa: BLE001 - surface any failure to UI
        job.update(status="error", message=str(e)[:400])
    finally:
        # drop the heavy source file once clips exist
        src = job_dir / "source.mp4"
        if src.exists() and job.get("status") == "done":
            try:
                src.unlink()
            except OSError:
                pass


def subprocess_run_ffprobe(path: Path) -> float:
    import subprocess
    p = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return round(float(p.stdout.strip()), 2)
    except ValueError:
        return 0.0


@app.post("/api/split")
def api_split(body: SplitRequest):
    url = splitter.validate_url(body.url)
    job_id = uuid.uuid4().hex[:12]
    # grab title early so UI can show it
    try:
        info = splitter.analyze(url)
        title = info.get("title") or "video"
    except Exception:
        title = "video"

    JOBS[job_id] = {
        "id": job_id, "status": "queued", "progress": 0, "message": "Queued…",
        "seconds": body.seconds, "created": time.time(), "clips": [],
        "video_title": title,
    }
    threading.Thread(target=_worker, args=(job_id, url, body.seconds, body.accurate),
                     daemon=True).start()
    return {"job_id": job_id, "title": title}


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/download/{job_id}")
def api_zip(job_id: str):
    job = _done_job(job_id)
    job_dir = WORK_DIR / job_id
    zip_path = job_dir / f"{_safe(job['video_title'])}_clips.zip"
    if not zip_path.exists():
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for clip in sorted((job_dir / "clips").glob("clip_*.mp4")):
                zf.write(clip, arcname=clip.name)
    return FileResponse(zip_path, filename=zip_path.name, media_type="application/zip")


@app.get("/api/download/{job_id}/{index}")
def api_single(job_id: str, index: int):
    job = _done_job(job_id)
    if index < 1 or index > len(job["clips"]):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = sorted((WORK_DIR / job_id / "clips").glob("clip_*.mp4"))[index - 1]
    return FileResponse(clip, filename=clip.name, media_type="video/mp4")


@app.get("/api/presets")
def api_presets():
    return {"presets": PRESETS}


@app.delete("/api/job/{job_id}")
def api_delete(job_id: str):
    if job_id in JOBS:
        shutil.rmtree(WORK_DIR / job_id, ignore_errors=True)
        del JOBS[job_id]
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Job not found")


def _done_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, not done yet.")
    return job


def _safe(name: str) -> str:
    keep = [c if (c.isalnum() or c in " ._-") else "_" for c in name]
    return "".join(keep)[:60] or "clips"


# static UI last so /api/* routes win
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


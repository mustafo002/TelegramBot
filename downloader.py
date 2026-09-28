import os
import re
import shutil
import threading
import time
from pathlib import Path

import yt_dlp

from config import (
    DOWNLOAD_DIR, TMP_DIR, MAX_UPLOAD_BYTES, SOCKET_TIMEOUT,
    RETRIES, FRAGMENT_RETRIES
)

ACTIVE = {}
ACTIVE_LOCK = threading.RLock()

def human_bytes(n):
    if n is None:
        return "?"
    n = float(n)
    units = ["B", "KB", "MB", "GB", "TB"]
    for u in units:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def human_time(seconds):
    if seconds is None:
        return "?"
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds//60}m {seconds%60:02d}s"

def safe_title(s):
    s = re.sub(r"[^\w\s.-]", "_", s or "video", flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()[:100] or "video"

def extract_info(url):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": RETRIES,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def _format_for(kind, height, fps):
    if kind == "audio":
        return "bestaudio/best"

    # Prefer requested FPS/height, then gracefully fall back.
    return (
        f"bestvideo[height<={height}][fps>={fps}]+bestaudio/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
    )

def download(url, user_id, kind="video", height=1080, fps=30, progress_cb=None):
    job_dir = TMP_DIR / f"{user_id}_{int(time.time()*1000)}"
    job_dir.mkdir(parents=True, exist_ok=True)

    state = {"cancel": False, "path": None}
    with ACTIVE_LOCK:
        ACTIVE[user_id] = state

    def hook(d):
        if state["cancel"]:
            raise yt_dlp.utils.DownloadError("CANCELLED")

        if progress_cb:
            status = d.get("status")
            progress_cb({
                "status": status,
                "downloaded": d.get("downloaded_bytes", 0),
                "total": d.get("total_bytes") or d.get("total_bytes_estimate"),
                "speed": d.get("speed"),
                "eta": d.get("eta"),
                "filename": d.get("filename"),
            })

    outtmpl = str(job_dir / "%(title).120B [%(id)s].%(ext)s")

    opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": RETRIES,
        "fragment_retries": FRAGMENT_RETRIES,
        "continuedl": True,
        "overwrites": False,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "restrictfilenames": False,
        "concurrent_fragment_downloads": 4,
    }

    if kind == "audio":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        opts["format"] = _format_for(kind, int(height), int(fps))

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = Path(ydl.prepare_filename(info))
            candidates = list(job_dir.glob("*"))
            if kind == "audio":
                mp3s = list(job_dir.glob("*.mp3"))
                path = mp3s[0] if mp3s else (candidates[0] if candidates else requested)
            else:
                mp4s = list(job_dir.glob("*.mp4"))
                path = mp4s[0] if mp4s else (candidates[0] if candidates else requested)

        if not path or not path.exists():
            raise RuntimeError("Downloaded file was not found.")

        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise RuntimeError(
                f"File is {human_bytes(path.stat().st_size)}, "
                f"above configured upload limit {human_bytes(MAX_UPLOAD_BYTES)}."
            )

        final_name = safe_title(info.get("title")) + path.suffix
        final_path = DOWNLOAD_DIR / final_name
        if final_path.exists():
            final_path = DOWNLOAD_DIR / f"{safe_title(info.get('title'))}_{info.get('id','file')}{path.suffix}"

        shutil.move(str(path), str(final_path))
        state["path"] = final_path
        return info, final_path

    finally:
        with ACTIVE_LOCK:
            ACTIVE.pop(user_id, None)
        shutil.rmtree(job_dir, ignore_errors=True)

def cancel(user_id):
    with ACTIVE_LOCK:
        state = ACTIVE.get(user_id)
        if state:
            state["cancel"] = True
            return True
    return False

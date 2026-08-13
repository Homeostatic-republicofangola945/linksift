import glob
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DEFAULT_DOWNLOAD_TIMEOUT = 3600
DEFAULT_MAX_CONCURRENT_DOWNLOADS = 3
DEFAULT_JOB_TTL = 86400
DEFAULT_MAX_PLAYLIST_ITEMS = 200
CLEANUP_INTERVAL_SECONDS = 600
PROGRESS_PREFIX = "__LINKSIFT_PROGRESS__"
TERMINAL_STATUSES = frozenset({"done", "error", "cancelled", "timed_out"})
ACTIVE_STATUSES = frozenset({"downloading", "cancelling"})
JOB_FILE_NAME_PATTERN = re.compile(r"^[0-9a-f]{10}\.")

jobs = {}
processes = {}
jobs_lock = threading.Lock()
cleanup_lock = threading.Lock()
last_cleanup_monotonic = None


def get_missing_runtime_tools(required_tools):
    return [tool for tool in required_tools if shutil.which(tool) is None]


def runtime_unavailable_response(required_tools):
    missing_tools = get_missing_runtime_tools(required_tools)
    if not missing_tools:
        return None
    tool_list = ", ".join(missing_tools)
    return jsonify({
        "error": f"Server downloader is unavailable. Start LinkSift with Docker Compose or install: {tool_list}.",
        "missing_tools": missing_tools,
    }), 503


def subprocess_unavailable_response(required_tools):
    tool_list = ", ".join(required_tools)
    return jsonify({
        "error": f"Server downloader is unavailable. Start LinkSift with Docker Compose or install: {tool_list}.",
        "missing_tools": list(required_tools),
    }), 503


def get_positive_int(name, default):
    """Return a positive integer environment setting or its default."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def get_download_timeout():
    return get_positive_int("LINKSIFT_DOWNLOAD_TIMEOUT", DEFAULT_DOWNLOAD_TIMEOUT)


def get_max_concurrent_downloads():
    return get_positive_int("LINKSIFT_MAX_CONCURRENT_DOWNLOADS", DEFAULT_MAX_CONCURRENT_DOWNLOADS)


def get_job_ttl():
    return get_positive_int("LINKSIFT_JOB_TTL", DEFAULT_JOB_TTL)


def get_max_playlist_items():
    return get_positive_int("LINKSIFT_MAX_PLAYLIST_ITEMS", DEFAULT_MAX_PLAYLIST_ITEMS)


def active_download_count():
    """Count jobs still holding a slot: cancelling keeps its slot until the
    download thread finalizes the job, because the process may still run."""
    return sum(job.get("status") in ACTIVE_STATUSES for job in jobs.values())


def get_request_data():
    """Return a JSON object request body or a stable validation error."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Request body must be a JSON object"}), 400)
    return data, None


def get_url(data):
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    return url.strip()


def parse_ytdlp_json(stdout):
    """Return the first valid JSON object emitted by yt-dlp."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("yt-dlp returned no JSON object")


def finite_number(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def update_job_progress(job, line):
    """Parse one yt-dlp progress line and update a job in place."""
    if not line.startswith(PROGRESS_PREFIX):
        return False
    try:
        progress = json.loads(line[len(PROGRESS_PREFIX):])
    except (TypeError, ValueError):
        return False
    if not isinstance(progress, dict):
        return False

    downloaded = max(0, finite_number(progress.get("downloaded_bytes"), 0))
    total = finite_number(progress.get("total_bytes"))
    if total is None:
        total = finite_number(progress.get("total_bytes_estimate"))
    if total is not None:
        total = max(0, total)

    speed = finite_number(progress.get("speed"))
    eta = finite_number(progress.get("eta"))
    percent = round(min(100, downloaded * 100 / total), 1) if total else None
    job.update({
        "phase": "downloading",
        "downloaded_bytes": downloaded,
        "total_bytes": total,
        "speed": speed,
        "eta": eta,
        "percent": percent,
    })
    return True


def terminate_process_tree(process):
    """Best-effort termination of a subprocess and its children (ffmpeg)."""
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def cancel_requested(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def run_download_command(cmd, job, timeout):
    """Run yt-dlp while streaming machine-readable progress into a job."""
    popen_kwargs = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
        **popen_kwargs,
    )
    job_id = job.get("id")
    with jobs_lock:
        processes[job_id] = process
        already_cancelled = bool(job.get("cancel_requested"))
    if already_cancelled:
        terminate_process_tree(process)
    diagnostics = deque(maxlen=200)

    def read_output():
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not update_job_progress(job, line):
                diagnostics.append(line)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        process.wait(timeout=5)
        raise
    finally:
        reader.join(timeout=5)
        with jobs_lock:
            processes.pop(job_id, None)

    return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="\n".join(diagnostics))


def job_paths(job_id):
    return glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))


def is_intermediate_file(path):
    name = os.path.basename(path)
    return ".part" in name or name.endswith(".ytdl") or f".f" in name


def cleanup_job_files(job_id, include_final=False):
    """Remove only intermediate files belonging to a failed job."""
    for path in job_paths(job_id):
        if not include_final and not is_intermediate_file(path):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def select_output_file(job_id, format_choice):
    extension = ".mp3" if format_choice == "audio" else ".mp4"
    exact_output = os.path.join(DOWNLOAD_DIR, f"{job_id}{extension}")
    if os.path.isfile(exact_output):
        return exact_output

    candidates = [
        path
        for path in job_paths(job_id)
        if os.path.isfile(path)
        and not is_intermediate_file(path)
        and path.lower().endswith(extension)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def safe_download_name(title, path):
    extension = Path(path).suffix.lower()
    if not isinstance(title, str):
        title = ""
    safe_title = "".join(
        char for char in title
        if char.isprintable() and char not in r'\\/:*?"<>|'
    ).strip(". ")[:100]
    return f"{safe_title}{extension}" if safe_title else os.path.basename(path)


def finish_job_cancelled(job_id):
    """Remove every file of a cancelled job and mark it terminal."""
    cleanup_job_files(job_id, include_final=True)
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job.update({
            "status": "cancelled",
            "phase": "cancelled",
            "speed": None,
            "eta": None,
            "percent": None,
            "error": None,
            "finished_at": time.time(),
        })


def set_error(job_id, message, status="error"):
    """Finalize a failed job; a pending cancellation always wins."""
    was_cancelled = False
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        was_cancelled = bool(job.get("cancel_requested"))
        if not was_cancelled:
            job.update({
                "status": status,
                "phase": status,
                "speed": None,
                "eta": None,
                "error": message,
                "finished_at": time.time(),
            })
    if was_cancelled:
        finish_job_cancelled(job_id)


def run_download(job_id, url, format_choice, format_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job["id"] = job_id
        cancelled_before_start = bool(job.get("cancel_requested"))
    if cancelled_before_start:
        finish_job_cancelled(job_id)
        return
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--progress",
        "--progress-template",
        f"download:{PROGRESS_PREFIX}%(progress)j",
        "-o",
        out_template,
    ]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    cmd += ["--", url]
    timeout = get_download_timeout()

    try:
        result = run_download_command(cmd, job, timeout)
        if cancel_requested(job_id):
            finish_job_cancelled(job_id)
            return
        if result.returncode != 0:
            cleanup_job_files(job_id)
            errors = result.stderr.strip().splitlines()
            set_error(job_id, errors[-1] if errors else "Download failed")
            return

        chosen = select_output_file(job_id, format_choice)
        if not chosen:
            cleanup_job_files(job_id)
            set_error(job_id, "Download completed but no final file was found")
            return

        final_size = os.path.getsize(chosen)
        filename = safe_download_name(job.get("title"), chosen)
        for path in job_paths(job_id):
            if path != chosen:
                try:
                    os.remove(path)
                except OSError:
                    pass

        with jobs_lock:
            cancelled_at_finish = bool(job.get("cancel_requested"))
            if not cancelled_at_finish:
                job.update({
                    "phase": "done",
                    "file": chosen,
                    "filename": filename,
                    "downloaded_bytes": final_size,
                    "total_bytes": final_size,
                    "speed": None,
                    "eta": None,
                    "percent": 100.0,
                    "status": "done",
                    "finished_at": time.time(),
                })
        if cancelled_at_finish:
            finish_job_cancelled(job_id)
    except subprocess.TimeoutExpired:
        if cancel_requested(job_id):
            finish_job_cancelled(job_id)
            return
        cleanup_job_files(job_id)
        set_error(job_id, f"Download timed out after {timeout} seconds", status="timed_out")
    except FileNotFoundError:
        cleanup_job_files(job_id)
        set_error(job_id, "Server downloader became unavailable. Restart LinkSift with Docker Compose.")
    except Exception:
        app.logger.exception("Download failed unexpectedly for job %s", job_id)
        if cancel_requested(job_id):
            finish_job_cancelled(job_id)
            return
        cleanup_job_files(job_id)
        set_error(job_id, "Download failed unexpectedly")


def cleanup_orphan_files(now=None, ttl=None):
    """Delete stale LinkSift-named files that no known job owns."""
    if now is None:
        now = time.time()
    if ttl is None:
        ttl = get_job_ttl()
    with jobs_lock:
        known_jobs = set(jobs)
    try:
        names = os.listdir(DOWNLOAD_DIR)
    except OSError:
        app.logger.warning("Could not scan downloads directory for cleanup", exc_info=True)
        return
    for name in names:
        if not JOB_FILE_NAME_PATTERN.match(name) or name[:10] in known_jobs:
            continue
        path = os.path.join(DOWNLOAD_DIR, name)
        try:
            if not os.path.isfile(path) or now - os.path.getmtime(path) < ttl:
                continue
            os.remove(path)
        except OSError:
            app.logger.warning("Could not remove orphan file %s", name, exc_info=True)


def run_cleanup(now=None):
    """Expire terminal jobs past their TTL, then sweep orphan files."""
    if now is None:
        now = time.time()
    ttl = get_job_ttl()
    expired = []
    with jobs_lock:
        for job_id, job in list(jobs.items()):
            if job.get("status") not in TERMINAL_STATUSES:
                continue
            finished_at = job.get("finished_at") or job.get("created_at")
            if finished_at is None or now - finished_at < ttl:
                continue
            expired.append(job_id)
            jobs.pop(job_id, None)
            processes.pop(job_id, None)
    for job_id in expired:
        cleanup_job_files(job_id, include_final=True)
    cleanup_orphan_files(now=now, ttl=ttl)


@app.before_request
def opportunistic_cleanup():
    """Run cleanup at most once per interval, without failing the request."""
    global last_cleanup_monotonic
    if not cleanup_lock.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if last_cleanup_monotonic is not None and now - last_cleanup_monotonic < CLEANUP_INTERVAL_SECONDS:
            return
        last_cleanup_monotonic = now
        run_cleanup()
    except Exception:
        app.logger.exception("LinkSift cleanup failed")
    finally:
        cleanup_lock.release()


def startup_cleanup():
    global last_cleanup_monotonic
    try:
        with cleanup_lock:
            last_cleanup_monotonic = time.monotonic()
            run_cleanup()
    except Exception:
        app.logger.exception("LinkSift startup cleanup failed")


startup_cleanup()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    missing_tools = get_missing_runtime_tools(("yt-dlp", "ffmpeg"))
    return jsonify({
        "status": "ok" if not missing_tools else "degraded",
        "missing_tools": missing_tools,
    })


@app.route("/api/info", methods=["POST"])
def get_info():
    data, error = get_request_data()
    if error:
        return error
    url = get_url(data)
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    unavailable = runtime_unavailable_response(("yt-dlp",))
    if unavailable:
        return unavailable

    cmd = ["yt-dlp", "--no-playlist", "-j", "--", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = parse_ytdlp_json(result.stdout)
        best_by_height = {}
        for item in info.get("formats", []):
            if not isinstance(item, dict):
                continue
            height = item.get("height")
            format_id = item.get("format_id")
            if isinstance(height, int) and isinstance(format_id, str) and item.get("vcodec", "none") != "none":
                bitrate = finite_number(item.get("tbr"), 0)
                if height not in best_by_height or bitrate > finite_number(best_by_height[height].get("tbr"), 0):
                    best_by_height[height] = item

        formats = [
            {"id": item["format_id"], "label": f"{height}p", "height": height}
            for height, item in best_by_height.items()
        ]
        formats.sort(key=lambda item: item["height"], reverse=True)
        return jsonify({
            "title": info.get("title", "") if isinstance(info.get("title"), str) else "",
            "thumbnail": info.get("thumbnail", "") if isinstance(info.get("thumbnail"), str) else "",
            "duration": finite_number(info.get("duration")),
            "uploader": info.get("uploader", "") if isinstance(info.get("uploader"), str) else "",
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except FileNotFoundError:
        return subprocess_unavailable_response(("yt-dlp",))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/playlist", methods=["POST"])
def get_playlist_info():
    data, error = get_request_data()
    if error:
        return error
    url = get_url(data)
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    unavailable = runtime_unavailable_response(("yt-dlp",))
    if unavailable:
        return unavailable

    limit = get_max_playlist_items()
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-items", f"1:{limit + 1}", "-J", "--", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)
        entries = info.get("entries") if isinstance(info, dict) else []
        if not isinstance(entries, list):
            entries = []
        urls = [
            entry["url"]
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"].strip()
        ]
        # Truncation is judged on the raw entry count so unavailable or
        # malformed entries cannot mask an oversized playlist.
        return jsonify({"urls": urls[:limit], "truncated": len(entries) > limit, "limit": limit})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist info"}), 400
    except FileNotFoundError:
        return subprocess_unavailable_response(("yt-dlp",))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data, error = get_request_data()
    if error:
        return error
    url = get_url(data)
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")
    if format_choice not in {"audio", "video"}:
        return jsonify({"error": "Format must be audio or video"}), 400
    if format_id is not None and not isinstance(format_id, str):
        return jsonify({"error": "Format ID must be a string"}), 400
    if not isinstance(title, str):
        return jsonify({"error": "Title must be a string"}), 400

    unavailable = runtime_unavailable_response(("yt-dlp", "ffmpeg"))
    if unavailable:
        return unavailable

    job_id = uuid.uuid4().hex[:10]
    with jobs_lock:
        if active_download_count() >= get_max_concurrent_downloads():
            return jsonify({"error": "Too many downloads in progress"}), 429
        jobs[job_id] = {
            "id": job_id,
            "status": "downloading",
            "phase": "starting",
            "url": url,
            "title": title,
            "downloaded_bytes": 0,
            "total_bytes": None,
            "speed": None,
            "eta": None,
            "percent": None,
            "cancel_requested": False,
            "created_at": time.time(),
            "finished_at": None,
        }
    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/download/<job_id>", methods=["DELETE"])
def cancel_download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Job not found"}), 404
        status = job.get("status")
        if status in TERMINAL_STATUSES:
            return jsonify({"status": status})
        job.update({
            "cancel_requested": True,
            "status": "cancelling",
            "phase": "cancelling",
            "speed": None,
            "eta": None,
        })
        process = processes.get(job_id)
    terminate_process_tree(process)
    return jsonify({"status": "cancelling"})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "phase": job.get("phase"),
        "downloaded_bytes": job.get("downloaded_bytes"),
        "total_bytes": job.get("total_bytes"),
        "speed": job.get("speed"),
        "eta": job.get("eta"),
        "percent": job.get("percent"),
        "error": job.get("error"),
        "filename": job.get("filename"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    path = job.get("file") if job else None
    filename = job.get("filename") if job else None
    download_root = os.path.realpath(DOWNLOAD_DIR)
    if (
        not job
        or job.get("status") != "done"
        or not isinstance(path, str)
        or not isinstance(filename, str)
        or not os.path.isfile(path)
        or os.path.commonpath([download_root, os.path.realpath(path)]) != download_root
    ):
        return jsonify({"error": "File not ready"}), 404
    return send_file(path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)

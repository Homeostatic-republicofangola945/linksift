import glob
import json
import math
import os
import shutil
import subprocess
import threading
import uuid
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DEFAULT_DOWNLOAD_TIMEOUT = 3600
DEFAULT_MAX_CONCURRENT_DOWNLOADS = 3
PROGRESS_PREFIX = "__LINKSIFT_PROGRESS__"

jobs = {}
jobs_lock = threading.Lock()


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


def active_download_count():
    return sum(job.get("status") == "downloading" for job in jobs.values())


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


def run_download_command(cmd, job, timeout):
    """Run yt-dlp while streaming machine-readable progress into a job."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )
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
        process.kill()
        process.wait(timeout=5)
        raise
    finally:
        reader.join(timeout=5)

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


def set_error(job, message):
    job.update({
        "status": "error",
        "phase": "error",
        "speed": None,
        "eta": None,
        "error": message,
    })


def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
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
        if result.returncode != 0:
            cleanup_job_files(job_id)
            errors = result.stderr.strip().splitlines()
            set_error(job, errors[-1] if errors else "Download failed")
            return

        chosen = select_output_file(job_id, format_choice)
        if not chosen:
            cleanup_job_files(job_id)
            set_error(job, "Download completed but no final file was found")
            return

        final_size = os.path.getsize(chosen)
        filename = safe_download_name(job.get("title"), chosen)
        for path in job_paths(job_id):
            if path != chosen:
                try:
                    os.remove(path)
                except OSError:
                    pass

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
        })
    except subprocess.TimeoutExpired:
        cleanup_job_files(job_id)
        set_error(job, f"Download timed out after {timeout} seconds")
    except FileNotFoundError:
        cleanup_job_files(job_id)
        set_error(job, "Server downloader became unavailable. Restart LinkSift with Docker Compose.")
    except Exception as exc:
        cleanup_job_files(job_id)
        set_error(job, str(exc))


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

    cmd = ["yt-dlp", "--flat-playlist", "-J", "--", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)
        entries = info.get("entries", []) if isinstance(info, dict) else []
        urls = [entry["url"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("url"), str)]
        return jsonify({"urls": urls})
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
            "status": "downloading",
            "phase": "starting",
            "url": url,
            "title": title,
            "downloaded_bytes": 0,
            "total_bytes": None,
            "speed": None,
            "eta": None,
            "percent": None,
        }
    thread = threading.Thread(target=run_download, args=(job_id, url, format_choice, format_id), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


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

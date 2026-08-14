import contextlib
import glob
import importlib.metadata
import importlib.util
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
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DEFAULT_DOWNLOAD_TIMEOUT = 3600
DEFAULT_MAX_CONCURRENT_DOWNLOADS = 3
MAX_CONCURRENT_DOWNLOADS = 16
DEFAULT_MAX_QUEUED_DOWNLOADS = 200
DEFAULT_CONCURRENT_FRAGMENTS = 4
MAX_CONCURRENT_FRAGMENTS = 16
DEFAULT_JOB_TTL = 86400
DEFAULT_MAX_PLAYLIST_ITEMS = 200
DEFAULT_JOB_RETRIES = 2
MAX_JOB_RETRIES = 5
DEFAULT_RETRY_BASE_DELAY = 2
MAX_RETRY_BACKOFF_SECONDS = 15
CLEANUP_INTERVAL_SECONDS = 600
PROGRESS_PREFIX = "__LINKSIFT_PROGRESS__"
POSTPROCESS_PREFIX = "__LINKSIFT_POSTPROCESS__"
TERMINAL_STATUSES = frozenset({"done", "error", "cancelled", "timed_out"})
ACTIVE_STATUSES = frozenset({"downloading", "cancelling"})
JOB_FILE_NAME_PATTERN = re.compile(r"^[0-9a-f]{10}\.")

jobs = {}
processes = {}
jobs_lock = threading.Lock()
cleanup_lock = threading.Lock()
last_cleanup_monotonic = None
scheduler = None
scheduler_guard = threading.Lock()


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
    """Download worker slots, clamped to 1..16 so an environment typo cannot
    ask the scheduler for thousands of threads."""
    value = get_positive_int("LINKSIFT_MAX_CONCURRENT_DOWNLOADS", DEFAULT_MAX_CONCURRENT_DOWNLOADS)
    return min(value, MAX_CONCURRENT_DOWNLOADS)


def get_max_queued_downloads():
    return get_positive_int("LINKSIFT_MAX_QUEUED_DOWNLOADS", DEFAULT_MAX_QUEUED_DOWNLOADS)


def get_concurrent_fragments():
    """Fragment parallelism inside one yt-dlp download, clamped to 1..16."""
    value = get_positive_int("LINKSIFT_CONCURRENT_FRAGMENTS", DEFAULT_CONCURRENT_FRAGMENTS)
    return min(value, MAX_CONCURRENT_FRAGMENTS)


def get_job_retries():
    """Extra fresh-extraction attempts after the first one (0..5)."""
    raw = os.environ.get("LINKSIFT_JOB_RETRIES")
    if raw is None:
        return DEFAULT_JOB_RETRIES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_JOB_RETRIES
    if value < 0 or value > MAX_JOB_RETRIES:
        return DEFAULT_JOB_RETRIES
    return value


def get_retry_base_delay():
    """Seconds before the first retry; doubles per retry, capped elsewhere."""
    raw = os.environ.get("LINKSIFT_RETRY_BASE_DELAY")
    if raw is None:
        return DEFAULT_RETRY_BASE_DELAY
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_BASE_DELAY
    if not math.isfinite(value) or value < 0:
        return DEFAULT_RETRY_BASE_DELAY
    return value


def retry_backoff_delay(retry_index, base_delay):
    """Exponential backoff before the Nth retry (1-based), capped."""
    return min(base_delay * (2 ** (retry_index - 1)), MAX_RETRY_BACKOFF_SECONDS)


def get_po_token_provider_url():
    """Validated PO Token provider base URL from the environment, or None."""
    raw = os.environ.get("LINKSIFT_PO_TOKEN_PROVIDER_URL", "").strip()
    if not raw:
        return None
    if any(char in raw for char in ";, \t\r\n"):
        app.logger.warning("Ignoring LINKSIFT_PO_TOKEN_PROVIDER_URL: value contains unsafe characters")
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        parsed = None
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname:
        app.logger.warning("Ignoring LINKSIFT_PO_TOKEN_PROVIDER_URL: expected an http(s) URL with a hostname")
        return None
    if parsed.username or parsed.password:
        # Credentials would leak into yt-dlp argv and diagnostics output.
        app.logger.warning("Ignoring LINKSIFT_PO_TOKEN_PROVIDER_URL: credentials in the URL are not supported")
        return None
    return raw


def get_pot_plugin_version():
    """Installed bgutil PO token plugin version, or None. Metadata lookup
    only: the plugin is never imported and no network request is made."""
    try:
        return importlib.metadata.version("bgutil-ytdlp-pot-provider")
    except importlib.metadata.PackageNotFoundError:
        return None


def has_pot_plugin():
    return get_pot_plugin_version() is not None


_pot_plugin_warning_emitted = False


def ytdlp_runtime_args():
    """Extractor/runtime arguments shared by every yt-dlp invocation.

    The provider arguments are only emitted when the URL is valid AND the
    plugin is actually installed; otherwise they would be dead options."""
    global _pot_plugin_warning_emitted
    provider_url = get_po_token_provider_url()
    if not provider_url:
        return []
    if not has_pot_plugin():
        if not _pot_plugin_warning_emitted:
            _pot_plugin_warning_emitted = True
            app.logger.warning(
                "LINKSIFT_PO_TOKEN_PROVIDER_URL is set but the bgutil-ytdlp-pot-provider "
                "plugin is not installed — ignoring the provider. This usually means the "
                "default image is running; build the youtube-robust target instead."
            )
        return []
    return ["--extractor-args", f"youtubepot-bgutilhttp:base_url={provider_url}"]


def has_js_runtime():
    return shutil.which("deno") is not None


def has_ejs_support():
    return importlib.util.find_spec("yt_dlp_ejs") is not None


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


def parse_progress_payload(line, prefix):
    """Return the JSON object carried by a prefixed progress line, else None."""
    if not line.startswith(prefix):
        return None
    try:
        payload = json.loads(line[len(prefix):])
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def update_job_progress(job, line):
    """Parse one yt-dlp download progress line and update a job in place."""
    progress = parse_progress_payload(line, PROGRESS_PREFIX)
    if progress is None:
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
    with jobs_lock:
        # Terminal states are sticky: a late progress line is consumed but ignored.
        if job.get("status") in TERMINAL_STATUSES:
            return True
        job.update({
            "phase": "downloading",
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "speed": speed,
            "eta": eta,
            "percent": percent,
        })
    return True


def update_job_postprocess(job, line):
    """Parse one yt-dlp postprocess progress line and mark the job as processing."""
    if parse_progress_payload(line, POSTPROCESS_PREFIX) is None:
        return False
    with jobs_lock:
        if job.get("status") in TERMINAL_STATUSES:
            return True
        job.update({
            "phase": "processing",
            "speed": None,
            "eta": None,
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


# A fresh yt-dlp run performs a new extraction (new signed media URLs and PO
# tokens), which is exactly what these transient failures need.
TRANSIENT_DOWNLOAD_ERROR_MARKERS = (
    "http error 403",
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "connection reset",
    "remote end closed connection",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "timed out",
)

# Failures a re-run cannot fix; checked before the transient markers.
PERMANENT_DOWNLOAD_ERROR_MARKERS = (
    "unsupported url",
    "is not a valid url",
    "video unavailable",
    "private video",
    "this video is private",
    "has been removed",
    "requested format is not available",
    "sign in to confirm",
    "login required",
    "--cookies",
    "members",
)


def is_transient_download_error(stderr_text):
    """Decide whether a failed yt-dlp attempt deserves a fresh extraction."""
    text = (stderr_text or "").lower()
    if not text:
        return False
    if any(marker in text for marker in PERMANENT_DOWNLOAD_ERROR_MARKERS):
        return False
    return any(marker in text for marker in TRANSIENT_DOWNLOAD_ERROR_MARKERS)


def wait_before_retry(job, delay, deadline):
    """Interruptible backoff wait between download attempts.

    Returns False when the job is cancelled or the total deadline expires
    before the delay elapses; True when the next attempt may start."""
    cancel_event = job.get("cancel_event")
    wait_until = time.monotonic() + delay
    while True:
        if job.get("cancel_requested"):
            return False
        now = time.monotonic()
        if now >= deadline:
            return False
        if now >= wait_until:
            return True
        slice_seconds = min(wait_until, deadline) - now
        if cancel_event is not None:
            if cancel_event.wait(timeout=slice_seconds):
                return False
        else:
            time.sleep(min(slice_seconds, 0.05))


class DownloadScheduler:
    """Fixed pool of daemon workers draining a bounded FIFO queue.

    All workers are started eagerly by start(), which rolls back completely
    (shuts down and joins any workers it already started) if a thread cannot
    be created, so a half-initialized pool is never left running. submit()
    is a pure queue operation and cannot raise.

    Lock ordering: jobs_lock -> scheduler_guard -> self._condition. Callers
    may hold jobs_lock while calling scheduler methods; scheduler methods
    never take jobs_lock themselves. When claim_lock is set (jobs_lock in
    production), workers acquire it BEFORE self._condition to pop a task and
    run the on_claim hook in one atomic step, so a reader holding claim_lock
    can never observe a job that is neither queued nor claimed. Both locks
    are released before the task itself runs.
    """

    def __init__(self, worker_count, queue_limit, thread_factory=None, claim_lock=None, on_claim=None, on_claim_error=None):
        self.worker_count = min(max(1, worker_count), MAX_CONCURRENT_DOWNLOADS)
        self.queue_limit = max(1, queue_limit)
        self._thread_factory = thread_factory or threading.Thread
        self._claim_lock = claim_lock
        self._on_claim = on_claim
        self._on_claim_error = on_claim_error
        self._condition = threading.Condition()
        self._queue = deque()
        self._workers = []
        self._shutdown = False

    def start(self):
        """Start every worker up front; on any failure roll back and re-raise."""
        if self._workers:
            return self
        try:
            for index in range(self.worker_count):
                worker = self._thread_factory(
                    target=self._worker_loop,
                    name=f"linksift-download-worker-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)
        except Exception:
            self.shutdown()
            raise
        return self

    def submit(self, job_id, task):
        """Queue a task; returns False when the queue is full or shut down."""
        with self._condition:
            if self._shutdown or len(self._queue) >= self.queue_limit:
                return False
            self._queue.append((job_id, task))
            self._condition.notify()
            return True

    def cancel_queued(self, job_id):
        """Remove a still-queued task; returns False once a worker owns it."""
        with self._condition:
            for index, (queued_id, _) in enumerate(self._queue):
                if queued_id == job_id:
                    del self._queue[index]
                    return True
            return False

    def queue_position(self, job_id):
        """1-based position among waiting tasks, or None when not queued."""
        with self._condition:
            for index, (queued_id, _) in enumerate(self._queue):
                if queued_id == job_id:
                    return index + 1
            return None

    def queued_count(self):
        with self._condition:
            return len(self._queue)

    def _claim_next(self):
        """Atomically pop the next task and run the claim hook.

        Holding claim_lock across the pop and the hook is what removes the
        dequeue/status race: under that lock a job is either still in the
        queue (status queued, position >= 1) or already claimed (status no
        longer queued). A raising claim hook is handled here too: the
        on_claim_error hook finalizes the job under the SAME locks, so the
        invariant survives hook exceptions; logging happens only after the
        locks are released. Returns None when there is nothing to run.
        """
        outer = self._claim_lock if self._claim_lock is not None else contextlib.nullcontext()
        claim_error = None
        error_hook_failure = None
        job_id = None
        with outer:
            with self._condition:
                if self._shutdown or not self._queue:
                    return None
                job_id, task = self._queue.popleft()
                if self._on_claim is not None:
                    try:
                        if not self._on_claim(job_id):
                            return None
                    except Exception as exc:
                        claim_error = exc
                        if self._on_claim_error is not None:
                            try:
                                self._on_claim_error(job_id, exc)
                            except Exception as hook_exc:
                                error_hook_failure = hook_exc
        if claim_error is not None:
            app.logger.error(
                "Claim hook failed; job %s was marked as failed without starting",
                job_id,
                exc_info=claim_error,
            )
            if error_hook_failure is not None:
                app.logger.error(
                    "Claim-error hook itself failed for job %s",
                    job_id,
                    exc_info=error_hook_failure,
                )
            return None
        return job_id, task

    def _worker_loop(self):
        while True:
            with self._condition:
                while not self._queue and not self._shutdown:
                    self._condition.wait()
                if self._shutdown:
                    return
            try:
                claimed = self._claim_next()
            except Exception:
                # Last line of defense: a worker must never die.
                app.logger.exception("Download worker failed while claiming a task")
                continue
            if claimed is None:
                continue
            _, task = claimed
            try:
                task()
            except Exception:
                app.logger.exception("Download worker task failed")

    def shutdown(self, wait_seconds=5):
        """Stop workers and drop queued tasks. Never call under jobs_lock."""
        with self._condition:
            self._shutdown = True
            self._queue.clear()
            self._condition.notify_all()
        for worker in self._workers:
            worker.join(timeout=wait_seconds)


def claim_download_job(job_id):
    """Scheduler claim hook; runs under jobs_lock and the scheduler lock.

    Returns False to drop the task without running it. Must never leave a
    popped job in status "queued": /api/status treats queued as "still in
    the scheduler queue" when computing queue_position.
    """
    job = jobs.get(job_id)
    if job is None or job.get("status") in TERMINAL_STATUSES:
        return False
    if job.get("cancel_requested"):
        # run_download finalizes the cancellation without spawning yt-dlp.
        if job.get("status") == "queued":
            job.update({"status": "cancelling", "phase": "cancelling"})
        return True
    job.update({
        "status": "downloading",
        "phase": "starting",
        "started_at": time.time(),
    })
    return True


def fail_claimed_job(job_id, exc):
    """Claim-error hook; runs under jobs_lock and the scheduler lock.

    Must not re-acquire jobs_lock (it is not reentrant), log, or do I/O.
    The exception stays out of the job: clients get a stable message."""
    job = jobs.get(job_id)
    if job is None or job.get("status") in TERMINAL_STATUSES:
        return
    job.update({
        "status": "error",
        "phase": "error",
        "error": "Download could not be started",
        "speed": None,
        "eta": None,
        "percent": None,
        "finished_at": time.time(),
    })


def get_scheduler():
    """Return the shared scheduler, creating and starting it from current
    settings. The global is only published after start() succeeds, so a
    failed startup leaves it None and the next request retries."""
    global scheduler
    with scheduler_guard:
        if scheduler is None:
            scheduler = DownloadScheduler(
                get_max_concurrent_downloads(),
                get_max_queued_downloads(),
                claim_lock=jobs_lock,
                on_claim=claim_download_job,
                on_claim_error=fail_claimed_job,
            ).start()
        return scheduler


def reset_scheduler():
    """Shut down and drop the scheduler; intended for tests. Never call under jobs_lock."""
    global scheduler
    with scheduler_guard:
        current, scheduler = scheduler, None
    if current is not None:
        current.shutdown()


def run_download_command(cmd, job, timeout):
    """Run yt-dlp while streaming machine-readable progress into a job.

    Returns None without spawning anything when the job was already
    cancelled. The cancellation check, process creation and registry insert
    happen in ONE jobs_lock critical section, so a concurrent DELETE either
    runs first (no process is ever spawned) or runs after (it sees the
    registered process and terminates it). The lock is never held while the
    process runs or its output is read."""
    popen_kwargs = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    job_id = job.get("id")
    with jobs_lock:
        if job.get("cancel_requested"):
            return None
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            **popen_kwargs,
        )
        processes[job_id] = process
    diagnostics = deque(maxlen=200)

    def read_output():
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if update_job_progress(job, line) or update_job_postprocess(job, line):
                continue
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
    max_attempts = get_job_retries() + 1
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None or job.get("status") in TERMINAL_STATUSES:
            # A cancelled queued job may still be seen by a waking worker.
            return
        job["id"] = job_id
        cancelled_before_start = bool(job.get("cancel_requested"))
        if not cancelled_before_start:
            job.update({
                "status": "downloading",
                "phase": "starting",
                "started_at": time.time(),
                "attempt": 1,
                "max_attempts": max_attempts,
            })
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
        "--progress-template",
        f"postprocess:{POSTPROCESS_PREFIX}%(progress)j",
        "--concurrent-fragments",
        str(get_concurrent_fragments()),
        "--continue",
        "--part",
        "--retries",
        "10",
        "--fragment-retries",
        "10",
        "--extractor-retries",
        "3",
        "--retry-sleep",
        "http:exp=1:20",
        "--retry-sleep",
        "fragment:exp=1:20",
        *ytdlp_runtime_args(),
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
    base_delay = get_retry_base_delay()
    deadline = time.monotonic() + timeout
    attempt = 1
    attempt_timeout = timeout

    try:
        while True:
            # Each attempt re-runs the full yt-dlp command, forcing a fresh
            # extraction (new signed media URLs) with the same format choice.
            # None means the pre-spawn gate refused a cancelled job.
            result = run_download_command(cmd, job, attempt_timeout)
            if result is None or cancel_requested(job_id):
                finish_job_cancelled(job_id)
                return
            if result.returncode == 0:
                break
            errors = result.stderr.strip().splitlines()
            message = errors[-1] if errors else "Download failed"
            if attempt >= max_attempts or not is_transient_download_error(result.stderr):
                cleanup_job_files(job_id)
                set_error(job_id, message)
                return
            delay = retry_backoff_delay(attempt, base_delay)
            attempt += 1
            with jobs_lock:
                current = jobs.get(job_id)
                if current is None or current.get("status") in TERMINAL_STATUSES:
                    return
                current.update({
                    "phase": "retrying",
                    "error": None,
                    "speed": None,
                    "eta": None,
                    "attempt": attempt,
                })
            app.logger.warning(
                "Transient download failure for job %s; retrying with a fresh extraction (attempt %d/%d): %s",
                job_id,
                attempt,
                max_attempts,
                message,
            )
            # Intermediate .part files are intentionally kept so the next
            # attempt can resume; the total deadline keeps ticking.
            if not wait_before_retry(job, delay, deadline):
                if cancel_requested(job_id):
                    finish_job_cancelled(job_id)
                    return
                cleanup_job_files(job_id)
                set_error(job_id, f"Download timed out after {timeout} seconds", status="timed_out")
                return
            if cancel_requested(job_id):
                finish_job_cancelled(job_id)
                return
            attempt_timeout = deadline - time.monotonic()
            if attempt_timeout <= 0:
                cleanup_job_files(job_id)
                set_error(job_id, f"Download timed out after {timeout} seconds", status="timed_out")
                return
            with jobs_lock:
                current = jobs.get(job_id)
                if current is None or current.get("status") in TERMINAL_STATUSES:
                    return
                # A cancelling job must never flip back to "starting".
                cancelled_before_attempt = bool(current.get("cancel_requested"))
                if not cancelled_before_attempt:
                    current.update({"phase": "starting"})
            if cancelled_before_attempt:
                finish_job_cancelled(job_id)
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
    active_scheduler = scheduler
    if active_scheduler is not None:
        for job_id in expired:
            active_scheduler.cancel_queued(job_id)
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
    provider_configured = get_po_token_provider_url() is not None
    return jsonify({
        "status": "ok" if not missing_tools else "degraded",
        "missing_tools": missing_tools,
        # Additive, informational only: a missing capability degrades YouTube
        # reliability but is never fatal on its own. "configured" means the
        # environment URL is valid; "po_token_provider" additionally requires
        # the plugin to be installed. No live probe of the provider is made.
        "capabilities": {
            "youtube_js_runtime": has_js_runtime(),
            "youtube_ejs": has_ejs_support(),
            "po_token_provider_configured": provider_configured,
            "po_token_provider": provider_configured and has_pot_plugin(),
        },
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

    cmd = ["yt-dlp", "--no-playlist", "-j", *ytdlp_runtime_args(), "--", url]
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
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-items", f"1:{limit + 1}", "-J", *ytdlp_runtime_args(), "--", url]
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

    # Acquire the scheduler before creating the job and outside jobs_lock:
    # a worker-startup failure then leaves nothing behind to roll back.
    try:
        download_scheduler = get_scheduler()
    except Exception:
        app.logger.exception("Download scheduler failed to start")
        return jsonify({"error": "Download scheduler is unavailable"}), 503

    job_id = uuid.uuid4().hex[:10]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "phase": "queued",
            "url": url,
            "title": title,
            "downloaded_bytes": 0,
            "total_bytes": None,
            "speed": None,
            "eta": None,
            "percent": None,
            "cancel_requested": False,
            "cancel_event": threading.Event(),
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }
        # The job must exist before a worker can pick it up; if the queue is
        # full it is removed in the same critical section, leaving no orphan.
        accepted = download_scheduler.submit(
            job_id,
            lambda: run_download(job_id, url, format_choice, format_id),
        )
        if not accepted:
            jobs.pop(job_id, None)
    if not accepted:
        return jsonify({"error": "Download queue is full"}), 429
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
        job["cancel_requested"] = True
        cancel_event = job.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
        if status == "queued" and get_scheduler().cancel_queued(job_id):
            # Removed before any worker owned it: no subprocess ever exists.
            job.update({
                "status": "cancelled",
                "phase": "cancelled",
                "speed": None,
                "eta": None,
                "percent": None,
                "error": None,
                "finished_at": time.time(),
            })
            return jsonify({"status": "cancelled"})
        # Running, or lost the dequeue race: the owning worker observes
        # cancel_requested and finalizes the job as cancelled.
        job.update({
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
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        status = job["status"]
        payload = {
            "status": status,
            "phase": job.get("phase"),
            "downloaded_bytes": job.get("downloaded_bytes"),
            "total_bytes": job.get("total_bytes"),
            "speed": job.get("speed"),
            "eta": job.get("eta"),
            "percent": job.get("percent"),
            "error": job.get("error"),
            "filename": job.get("filename"),
            "queue_position": get_scheduler().queue_position(job_id) if status == "queued" else None,
            "started_at": job.get("started_at"),
            "attempt": job.get("attempt"),
            "max_attempts": job.get("max_attempts"),
        }
    return jsonify(payload)


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

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DownloadBackendTests(unittest.TestCase):
    def setUp(self):
        app.jobs.clear()
        self.client = app.app.test_client()

    def tearDown(self):
        app.jobs.clear()

    def test_post_endpoints_reject_non_object_json(self):
        for endpoint in ("/api/info", "/api/playlist", "/api/download"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, json=[])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"], "Request body must be a JSON object")

    def test_download_rejects_invalid_field_types(self):
        for payload in ({"url": None}, {"url": 3}, {"url": "https://example.test", "title": 3}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/download", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_download_command_ends_options_before_url(self):
        job_id = "argumentsep"
        app.jobs[job_id] = {"status": "downloading", "title": ""}
        finished = subprocess.CompletedProcess([], 1, "", "failed")
        with patch.object(app, "run_download_command", return_value=finished) as run:
            app.run_download(job_id, "--bad-option", "video", None)
        self.assertEqual(run.call_args.args[0][-2:], ["--", "--bad-option"])

    def test_parse_ytdlp_json_skips_warnings(self):
        self.assertEqual(
            app.parse_ytdlp_json('WARNING: ignored\n{"title": "valid"}'),
            {"title": "valid"},
        )

    def test_select_output_prefers_exact_merged_file(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(app, "DOWNLOAD_DIR", temp_dir):
            job_id = "mergedfile"
            stream = Path(temp_dir) / f"{job_id}.f137.mp4"
            merged = Path(temp_dir) / f"{job_id}.mp4"
            stream.write_bytes(b"video only")
            merged.write_bytes(b"video and audio")
            self.assertEqual(app.select_output_file(job_id, "video"), str(merged))

    def test_successful_download_keeps_exact_merged_file(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(app, "DOWNLOAD_DIR", temp_dir):
            job_id = "successfile"
            stream = Path(temp_dir) / f"{job_id}.f137.mp4"
            merged = Path(temp_dir) / f"{job_id}.mp4"
            stream.write_bytes(b"video only")
            merged.write_bytes(b"video and audio")
            app.jobs[job_id] = {"status": "downloading", "title": "Example"}
            with patch.object(app, "run_download_command", return_value=subprocess.CompletedProcess([], 0, "", "")):
                app.run_download(job_id, "https://example.test", "video", None)
            self.assertEqual(app.jobs[job_id]["file"], str(merged))
            self.assertTrue(merged.exists())
            self.assertFalse(stream.exists())

    def test_cleanup_runs_for_unexpected_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(app, "DOWNLOAD_DIR", temp_dir):
            job_id = "unexpected"
            partial = Path(temp_dir) / f"{job_id}.f1.mp4.part"
            partial.write_bytes(b"partial")
            app.jobs[job_id] = {"status": "downloading", "title": ""}
            with patch.object(app, "run_download_command", side_effect=RuntimeError("boom")):
                app.run_download(job_id, "https://example.test", "video", None)
            self.assertEqual(app.jobs[job_id]["status"], "error")
            self.assertFalse(partial.exists())

    def test_safe_filename_removes_control_characters(self):
        name = app.safe_download_name("Video\r\nTitle", "/tmp/file.mp4")
        self.assertEqual(name, "VideoTitle.mp4")
        self.assertNotIn("\r", name)
        self.assertNotIn("\n", name)

    def test_file_endpoint_returns_404_for_missing_or_incomplete_job(self):
        app.jobs["missing"] = {"status": "done", "file": "missing.mp4"}
        response = self.client.get("/api/file/missing")
        self.assertEqual(response.status_code, 404)

    def test_progress_is_clamped_and_never_marks_stream_done(self):
        job = {}
        line = app.PROGRESS_PREFIX + json.dumps({
            "status": "finished",
            "downloaded_bytes": 500,
            "total_bytes": 100,
        })
        self.assertTrue(app.update_job_progress(job, line))
        self.assertEqual(job["phase"], "downloading")
        self.assertEqual(job["percent"], 100.0)

    def test_concurrency_limit_returns_429(self):
        app.jobs["existing"] = {"status": "downloading"}
        with patch.object(app, "get_max_concurrent_downloads", return_value=1), patch.object(
            app, "runtime_unavailable_response", return_value=None
        ):
            response = self.client.post("/api/download", json={"url": "https://example.test"})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class JobTtlParsingTests(unittest.TestCase):
    def test_default_ttl_used_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_JOB_TTL", None)
            self.assertEqual(app.get_job_ttl(), 86400)

    def test_invalid_ttl_values_fall_back_to_default(self):
        for value in ("not-a-number", "0", "-5", "1.5", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_JOB_TTL": value}):
                self.assertEqual(app.get_job_ttl(), 86400)

    def test_valid_ttl_override_is_used(self):
        with patch.dict(os.environ, {"LINKSIFT_JOB_TTL": "120"}):
            self.assertEqual(app.get_job_ttl(), 120)


class JobCleanupTests(unittest.TestCase):
    def setUp(self):
        app.jobs.clear()
        app.processes.clear()

    def tearDown(self):
        app.jobs.clear()
        app.processes.clear()

    def test_cleanup_removes_expired_terminal_jobs_and_files(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DOWNLOAD_DIR", temp_dir
        ), patch.object(app, "get_job_ttl", return_value=100):
            for status, job_id in (("done", "aaaaaaaaaa"), ("error", "bbbbbbbbbb"), ("cancelled", "cccccccccc")):
                (Path(temp_dir) / f"{job_id}.mp4").write_bytes(b"final")
                (Path(temp_dir) / f"{job_id}.f137.mp4.part").write_bytes(b"partial")
                app.jobs[job_id] = {"status": status, "created_at": now - 500, "finished_at": now - 200}
            app.run_cleanup(now=now)
            self.assertEqual(app.jobs, {})
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_cleanup_keeps_unexpired_terminal_jobs(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DOWNLOAD_DIR", temp_dir
        ), patch.object(app, "get_job_ttl", return_value=100):
            job_id = "dddddddddd"
            final = Path(temp_dir) / f"{job_id}.mp4"
            final.write_bytes(b"final")
            app.jobs[job_id] = {"status": "done", "created_at": now - 90, "finished_at": now - 50}
            app.run_cleanup(now=now)
            self.assertIn(job_id, app.jobs)
            self.assertTrue(final.exists())

    def test_cleanup_skips_active_downloads_and_their_files(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DOWNLOAD_DIR", temp_dir
        ), patch.object(app, "get_job_ttl", return_value=100):
            for status, job_id in (("downloading", "eeeeeeeeee"), ("cancelling", "abcdef1234")):
                partial = Path(temp_dir) / f"{job_id}.f137.mp4.part"
                partial.write_bytes(b"partial")
                os.utime(partial, (now - 1000, now - 1000))
                app.jobs[job_id] = {"status": status, "created_at": now - 1000, "finished_at": None}
            app.run_cleanup(now=now)
            self.assertIn("eeeeeeeeee", app.jobs)
            self.assertIn("abcdef1234", app.jobs)
            self.assertTrue((Path(temp_dir) / "eeeeeeeeee.f137.mp4.part").exists())
            self.assertTrue((Path(temp_dir) / "abcdef1234.f137.mp4.part").exists())

    def test_orphan_cleanup_removes_only_stale_linksift_files(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(app, "DOWNLOAD_DIR", temp_dir):
            def make_file(name, age_seconds):
                path = Path(temp_dir) / name
                path.write_bytes(b"data")
                os.utime(path, (now - age_seconds, now - age_seconds))
                return path

            old_orphan = make_file("1234567890.mp4", 1000)
            old_partial = make_file("0123abcdef.f137.mp4.part", 1000)
            fresh_orphan = make_file("fedcba9876.mp4", 10)
            foreign_old = make_file("keep-me.mp4", 1000)
            short_name = make_file("abc.mp4", 1000)
            owned = make_file("aaaa000000.mp4.part", 1000)
            app.jobs["aaaa000000"] = {"status": "downloading", "created_at": now - 1000}

            app.cleanup_orphan_files(now=now, ttl=100)

            self.assertFalse(old_orphan.exists())
            self.assertFalse(old_partial.exists())
            self.assertTrue(fresh_orphan.exists())
            self.assertTrue(foreign_old.exists())
            self.assertTrue(short_name.exists())
            self.assertTrue(owned.exists())


if __name__ == "__main__":
    unittest.main()

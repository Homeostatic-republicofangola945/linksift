import os
import subprocess
import unittest
from unittest.mock import patch

import app


class ConcurrentFragmentsParsingTests(unittest.TestCase):
    def test_default_is_four(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_CONCURRENT_FRAGMENTS", None)
            self.assertEqual(app.get_concurrent_fragments(), 4)

    def test_valid_value_is_used(self):
        with patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": "8"}):
            self.assertEqual(app.get_concurrent_fragments(), 8)

    def test_invalid_string_falls_back_to_default(self):
        for value in ("abc", "", "2.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": value}):
                self.assertEqual(app.get_concurrent_fragments(), 4)

    def test_zero_falls_back_to_default(self):
        with patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": "0"}):
            self.assertEqual(app.get_concurrent_fragments(), 4)

    def test_negative_falls_back_to_default(self):
        with patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": "-3"}):
            self.assertEqual(app.get_concurrent_fragments(), 4)

    def test_oversized_value_is_clamped_to_sixteen(self):
        for value in ("17", "99", "1000"):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": value}):
                self.assertEqual(app.get_concurrent_fragments(), 16)

    def test_boundary_values_pass_through(self):
        with patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": "1"}):
            self.assertEqual(app.get_concurrent_fragments(), 1)
        with patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": "16"}):
            self.assertEqual(app.get_concurrent_fragments(), 16)


class ConcurrentDownloadsParsingTests(unittest.TestCase):
    def test_default_is_three(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_MAX_CONCURRENT_DOWNLOADS", None)
            self.assertEqual(app.get_max_concurrent_downloads(), 3)

    def test_valid_values_pass_through(self):
        for value in ("1", "8", "16"):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_MAX_CONCURRENT_DOWNLOADS": value}):
                self.assertEqual(app.get_max_concurrent_downloads(), int(value))

    def test_invalid_string_falls_back_to_default(self):
        for value in ("abc", "", "4.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_MAX_CONCURRENT_DOWNLOADS": value}):
                self.assertEqual(app.get_max_concurrent_downloads(), 3)

    def test_zero_falls_back_to_default(self):
        with patch.dict(os.environ, {"LINKSIFT_MAX_CONCURRENT_DOWNLOADS": "0"}):
            self.assertEqual(app.get_max_concurrent_downloads(), 3)

    def test_negative_falls_back_to_default(self):
        with patch.dict(os.environ, {"LINKSIFT_MAX_CONCURRENT_DOWNLOADS": "-2"}):
            self.assertEqual(app.get_max_concurrent_downloads(), 3)

    def test_oversized_values_clamp_to_sixteen(self):
        for value in ("17", "100", "10000"):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_MAX_CONCURRENT_DOWNLOADS": value}):
                self.assertEqual(app.get_max_concurrent_downloads(), 16)


class QueueLimitParsingTests(unittest.TestCase):
    def test_default_is_two_hundred(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_MAX_QUEUED_DOWNLOADS", None)
            self.assertEqual(app.get_max_queued_downloads(), 200)

    def test_valid_value_is_used(self):
        with patch.dict(os.environ, {"LINKSIFT_MAX_QUEUED_DOWNLOADS": "50"}):
            self.assertEqual(app.get_max_queued_downloads(), 50)

    def test_invalid_zero_and_negative_fall_back_to_default(self):
        for value in ("abc", "", "0", "-1", "3.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_MAX_QUEUED_DOWNLOADS": value}):
                self.assertEqual(app.get_max_queued_downloads(), 200)


class DownloadCommandFlagsTests(unittest.TestCase):
    def setUp(self):
        app.jobs.clear()
        app.processes.clear()

    def tearDown(self):
        app.jobs.clear()
        app.processes.clear()

    def _capture_command(self, url="https://example.test/video"):
        job_id = "cmdflags01"
        app.jobs[job_id] = {"status": "downloading", "title": ""}
        finished = subprocess.CompletedProcess([], 1, "", "failed")
        with patch.object(app, "run_download_command", return_value=finished) as run:
            app.run_download(job_id, url, "video", None)
        return run.call_args.args[0]

    def test_command_includes_configured_concurrent_fragments(self):
        with patch.dict(os.environ, {"LINKSIFT_CONCURRENT_FRAGMENTS": "8"}):
            cmd = self._capture_command()
        index = cmd.index("--concurrent-fragments")
        self.assertEqual(cmd[index + 1], "8")

    def test_command_includes_continue_and_part(self):
        cmd = self._capture_command()
        self.assertIn("--continue", cmd)
        self.assertIn("--part", cmd)

    def test_url_stays_after_argument_separator(self):
        cmd = self._capture_command(url="--evil-option")
        self.assertEqual(cmd[-2:], ["--", "--evil-option"])

    def test_no_external_downloader_is_configured(self):
        joined = " ".join(self._capture_command())
        self.assertNotIn("--external-downloader", joined)
        self.assertNotIn("aria2", joined)

    def test_command_includes_both_progress_templates(self):
        cmd = self._capture_command()
        self.assertIn(f"download:{app.PROGRESS_PREFIX}%(progress)j", cmd)
        self.assertIn(f"postprocess:{app.POSTPROCESS_PREFIX}%(progress)j", cmd)


if __name__ == "__main__":
    unittest.main()

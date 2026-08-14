import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


PROVIDER_URL = "http://bgutil-provider:4416"
PROVIDER_ARG = f"youtubepot-bgutilhttp:base_url={PROVIDER_URL}"


class ProviderUrlValidationTests(unittest.TestCase):
    def test_unset_or_blank_disables_the_provider(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_PO_TOKEN_PROVIDER_URL", None)
            self.assertIsNone(app.get_po_token_provider_url())
        with patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": "   "}):
            self.assertIsNone(app.get_po_token_provider_url())

    def test_valid_http_and_https_urls_are_accepted(self):
        for url in ("http://bgutil-provider:4416", "https://tokens.internal.example"):
            with self.subTest(url=url), patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": url}):
                self.assertEqual(app.get_po_token_provider_url(), url)

    def test_invalid_urls_are_rejected(self):
        for url in (
            "ftp://provider:4416",
            "provider:4416",
            "http://",
            "not a url",
            "http://host;youtube:player_client=web",
            "http://host,other",
            "http://host 4416",
            "file:///etc/passwd",
            "http://user:secret@host:4416",
            "https://token@host",
        ):
            with self.subTest(url=url), patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": url}):
                self.assertIsNone(app.get_po_token_provider_url())


class ProviderCommandWiringTests(unittest.TestCase):
    def setUp(self):
        app.jobs.clear()
        app.processes.clear()
        self.client = app.app.test_client()

    def tearDown(self):
        app.jobs.clear()
        app.processes.clear()

    def _info_command(self):
        with patch.object(app, "runtime_unavailable_response", return_value=None), patch.object(
            app.subprocess, "run"
        ) as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"title": "t", "formats": []}), "")
            self.client.post("/api/info", json={"url": "https://example.test/video"})
        return run.call_args.args[0]

    def _playlist_command(self):
        with patch.object(app, "runtime_unavailable_response", return_value=None), patch.object(
            app.subprocess, "run"
        ) as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"entries": []}), "")
            self.client.post("/api/playlist", json={"url": "https://example.test/playlist?list=x"})
        return run.call_args.args[0]

    def _download_command(self):
        job_id = "providercm"
        app.jobs[job_id] = {"status": "downloading", "title": ""}
        failed = subprocess.CompletedProcess([], 1, "", "ERROR: Video unavailable")
        # Retries pinned off so a classifier regression cannot add real backoff sleeps here.
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DOWNLOAD_DIR", temp_dir
        ), patch.dict(os.environ, {"LINKSIFT_JOB_RETRIES": "0"}), patch.object(
            app, "run_download_command", return_value=failed
        ) as run:
            app.run_download(job_id, "https://example.test/video", "video", None)
        return run.call_args.args[0]

    def test_provider_args_apply_to_all_ytdlp_commands_when_configured(self):
        with patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": PROVIDER_URL}), patch.object(
            app, "has_pot_plugin", return_value=True
        ):
            for name, cmd in (
                ("info", self._info_command()),
                ("playlist", self._playlist_command()),
                ("download", self._download_command()),
            ):
                with self.subTest(command=name):
                    index = cmd.index("--extractor-args")
                    self.assertEqual(cmd[index + 1], PROVIDER_ARG)
                    self.assertEqual(cmd[-2], "--")  # URL still after the separator

    def test_no_provider_args_when_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_PO_TOKEN_PROVIDER_URL", None)
            for name, cmd in (
                ("info", self._info_command()),
                ("playlist", self._playlist_command()),
                ("download", self._download_command()),
            ):
                with self.subTest(command=name):
                    self.assertNotIn("--extractor-args", cmd)

    def test_invalid_provider_url_cannot_inject_arguments(self):
        with patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": "http://host;youtube:player_client=web"}), patch.object(
            app, "has_pot_plugin", return_value=True
        ):
            cmd = self._download_command()
        self.assertNotIn("--extractor-args", cmd)
        self.assertFalse(any("player_client" in part for part in cmd))


class PotPluginDetectionTests(unittest.TestCase):
    def setUp(self):
        app._pot_plugin_warning_emitted = False

    def tearDown(self):
        app._pot_plugin_warning_emitted = False

    def test_plugin_version_detection(self):
        with patch.object(app.importlib.metadata, "version", return_value="1.3.1"):
            self.assertEqual(app.get_pot_plugin_version(), "1.3.1")
            self.assertTrue(app.has_pot_plugin())
        with patch.object(
            app.importlib.metadata, "version",
            side_effect=app.importlib.metadata.PackageNotFoundError("bgutil-ytdlp-pot-provider"),
        ):
            self.assertIsNone(app.get_pot_plugin_version())
            self.assertFalse(app.has_pot_plugin())

    def test_valid_url_without_plugin_disables_args_and_warns_once(self):
        with patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": PROVIDER_URL}), patch.object(
            app, "has_pot_plugin", return_value=False
        ):
            with self.assertLogs(app.app.logger, level="WARNING") as logs:
                self.assertEqual(app.ytdlp_runtime_args(), [])
                self.assertEqual(app.ytdlp_runtime_args(), [])
        warnings = [line for line in logs.output if "plugin is not installed" in line]
        self.assertEqual(len(warnings), 1)

    def test_valid_url_with_plugin_enables_args(self):
        with patch.dict(os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": PROVIDER_URL}), patch.object(
            app, "has_pot_plugin", return_value=True
        ):
            self.assertEqual(app.ytdlp_runtime_args(), ["--extractor-args", PROVIDER_ARG])

    def test_no_url_means_no_args_regardless_of_plugin(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(app, "has_pot_plugin", return_value=True):
            os.environ.pop("LINKSIFT_PO_TOKEN_PROVIDER_URL", None)
            self.assertEqual(app.ytdlp_runtime_args(), [])


class DownloadInternalRetryFlagsTests(unittest.TestCase):
    def setUp(self):
        app.jobs.clear()

    def tearDown(self):
        app.jobs.clear()

    def test_download_command_sets_internal_retry_defaults(self):
        job_id = "internalrt"
        app.jobs[job_id] = {"status": "downloading", "title": ""}
        failed = subprocess.CompletedProcess([], 1, "", "ERROR: Video unavailable")
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DOWNLOAD_DIR", temp_dir
        ), patch.dict(os.environ, {"LINKSIFT_JOB_RETRIES": "0"}), patch.object(
            app, "run_download_command", return_value=failed
        ) as run:
            app.run_download(job_id, "https://example.test/video", "video", None)
        cmd = run.call_args.args[0]
        for flag, value in (("--retries", "10"), ("--fragment-retries", "10"), ("--extractor-retries", "3")):
            index = cmd.index(flag)
            self.assertEqual(cmd[index + 1], value)
        self.assertIn("--retry-sleep", cmd)

    def test_metadata_commands_do_not_get_download_retry_flags(self):
        with patch.object(app, "runtime_unavailable_response", return_value=None), patch.object(
            app.subprocess, "run"
        ) as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"title": "t", "formats": []}), "")
            app.app.test_client().post("/api/info", json={"url": "https://example.test/video"})
        cmd = run.call_args.args[0]
        self.assertNotIn("--fragment-retries", cmd)


if __name__ == "__main__":
    unittest.main()

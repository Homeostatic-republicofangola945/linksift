import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class PlaylistLimitParsingTests(unittest.TestCase):
    def test_default_limit_used_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_MAX_PLAYLIST_ITEMS", None)
            self.assertEqual(app.get_max_playlist_items(), 200)

    def test_invalid_limit_values_fall_back_to_default(self):
        for value in ("not-a-number", "0", "-3", "2.5", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"LINKSIFT_MAX_PLAYLIST_ITEMS": value}):
                self.assertEqual(app.get_max_playlist_items(), 200)

    def test_valid_limit_override_is_used(self):
        with patch.dict(os.environ, {"LINKSIFT_MAX_PLAYLIST_ITEMS": "50"}):
            self.assertEqual(app.get_max_playlist_items(), 50)


class PlaylistEndpointLimitTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    @staticmethod
    def _playlist_json(count):
        return json.dumps({"entries": [{"url": f"https://example.test/v{i}"} for i in range(count)]})

    def _request_playlist(self, limit, entry_count):
        with patch.object(app, "runtime_unavailable_response", return_value=None), patch.object(
            app, "get_max_playlist_items", return_value=limit
        ), patch.object(app.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, self._playlist_json(entry_count), "")
            response = self.client.post("/api/playlist", json={"url": "https://example.test/playlist?list=x"})
        return response, run.call_args.args[0]

    def test_playlist_command_limits_items_server_side(self):
        response, cmd = self._request_playlist(limit=5, entry_count=3)
        self.assertEqual(response.status_code, 200)
        self.assertIn("--playlist-items", cmd)
        self.assertIn("1:6", cmd)

    def test_playlist_response_never_exceeds_limit(self):
        response, _ = self._request_playlist(limit=5, entry_count=6)
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["urls"]), 5)
        self.assertEqual(payload["limit"], 5)

    def test_truncated_flag_reflects_oversized_playlist(self):
        response, _ = self._request_playlist(limit=5, entry_count=6)
        self.assertTrue(response.get_json()["truncated"])

        response, _ = self._request_playlist(limit=5, entry_count=3)
        payload = response.get_json()
        self.assertFalse(payload["truncated"])
        self.assertEqual(len(payload["urls"]), 3)

    def _request_playlist_stdout(self, limit, stdout):
        with patch.object(app, "runtime_unavailable_response", return_value=None), patch.object(
            app, "get_max_playlist_items", return_value=limit
        ), patch.object(app.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout, "")
            return self.client.post("/api/playlist", json={"url": "https://example.test/playlist?list=x"})

    def test_truncated_counts_raw_entries_not_valid_urls(self):
        entries = [{"url": f"https://example.test/v{i}"} for i in range(5)] + [None]
        response = self._request_playlist_stdout(5, json.dumps({"entries": entries}))
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["urls"]), 5)
        self.assertTrue(payload["truncated"])

    def test_blank_and_malformed_entries_are_skipped_without_error(self):
        entries = [
            {"url": "https://example.test/v0"},
            {"url": "   "},
            {"url": ""},
            {"url": 3},
            {"title": "no url"},
            "not-a-dict",
            None,
        ]
        response = self._request_playlist_stdout(10, json.dumps({"entries": entries}))
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["urls"], ["https://example.test/v0"])
        self.assertFalse(payload["truncated"])

    def test_non_list_entries_payload_is_handled_safely(self):
        for entries in ({"a": 1}, "string", 5, None):
            with self.subTest(entries=entries):
                response = self._request_playlist_stdout(5, json.dumps({"entries": entries}))
                payload = response.get_json()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(payload["urls"], [])
                self.assertFalse(payload["truncated"])

    def test_frontend_shows_truncation_notice(self):
        template = (Path(app.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
        for token in ("data.truncated", "Showing the first"):
            with self.subTest(token=token):
                self.assertIn(token, template)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

import app


class ConcurrencySlotTests(unittest.TestCase):
    def setUp(self):
        app.jobs.clear()
        app.processes.clear()
        self.client = app.app.test_client()

    def tearDown(self):
        app.jobs.clear()
        app.processes.clear()

    def test_cancelling_job_holds_concurrency_slot(self):
        app.jobs["cancellin1"] = {"status": "cancelling"}
        with patch.object(app, "get_max_concurrent_downloads", return_value=1), patch.object(
            app, "runtime_unavailable_response", return_value=None
        ):
            response = self.client.post("/api/download", json={"url": "https://example.test/video"})
        self.assertEqual(response.status_code, 429)

    def test_slot_freed_once_job_is_cancelled(self):
        app.jobs["cancelled1"] = {"status": "cancelled"}
        with patch.object(app, "get_max_concurrent_downloads", return_value=1), patch.object(
            app, "runtime_unavailable_response", return_value=None
        ), patch.object(app.threading, "Thread"):
            response = self.client.post("/api/download", json={"url": "https://example.test/video"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("job_id", response.get_json())

    def test_active_download_count_includes_cancelling(self):
        app.jobs.update({
            "job1": {"status": "downloading"},
            "job2": {"status": "cancelling"},
            "job3": {"status": "done"},
            "job4": {"status": "error"},
            "job5": {"status": "cancelled"},
            "job6": {"status": "timed_out"},
        })
        self.assertEqual(app.active_download_count(), 2)


if __name__ == "__main__":
    unittest.main()

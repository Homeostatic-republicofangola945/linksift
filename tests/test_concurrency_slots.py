import threading
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
        """A worker stuck on a cancelling job keeps its slot, so the next job waits."""
        scheduler = app.DownloadScheduler(1, 5)
        release = threading.Event()
        cancelling_started = threading.Event()
        try:
            scheduler.start()
            def cancelling_job():
                cancelling_started.set()
                # Simulates waiting for the killed subprocess tree to exit.
                release.wait(timeout=10)

            self.assertTrue(scheduler.submit("cancelling", cancelling_job))
            self.assertTrue(cancelling_started.wait(timeout=5))
            self.assertTrue(scheduler.submit("waiting", lambda: None))
            self.assertEqual(scheduler.queue_position("waiting"), 1)
        finally:
            release.set()
            scheduler.shutdown()

    def test_new_download_accepted_after_job_is_cancelled(self):
        app.jobs["cancelled1"] = {"status": "cancelled"}

        class AcceptingScheduler:
            def submit(self, job_id, task):
                return True

        with patch.object(app, "get_scheduler", return_value=AcceptingScheduler()), patch.object(
            app, "runtime_unavailable_response", return_value=None
        ):
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

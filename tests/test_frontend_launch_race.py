import re
import unittest
from pathlib import Path

import app


def read_template():
    return (Path(app.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


def function_source(template, name):
    """Return the body of a top-level script function (4-space indent)."""
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(.*?)\n    \}}",
        template,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"function {name} not found in template")
    return match.group(1)


class LaunchRaceFrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.template = read_template()

    def test_pending_download_supports_deferred_cancellation(self):
        cancel_card = function_source(self.template, "cancelCard")
        self.assertIn("card.cancelRequested = true", cancel_card)
        self.assertIn("if (!card.jobId)", cancel_card)
        cancel_all = function_source(self.template, "cancelActiveDownloads")
        self.assertIn("card.cancelRequested = true", cancel_all)

    def test_late_job_id_after_reset_is_cancelled(self):
        dl_card = function_source(self.template, "dlCard")
        self.assertIn("isStaleLaunch(card, index, launchToken) || card.cancelRequested", dl_card)
        self.assertIn("cancelJobOnServer(data.job_id)", dl_card)

    def test_stale_response_is_not_attached_to_replacement_card(self):
        self.assertIn("function isStaleLaunch(card, index, launchToken)", self.template)
        dl_card = function_source(self.template, "dlCard")
        guard = dl_card.index("isStaleLaunch(card, index, launchToken) || card.cancelRequested")
        attach = dl_card.index("card.jobId = data.job_id;")
        poll = dl_card.index("pollCard(index, card);")
        self.assertLess(guard, attach)
        self.assertLess(guard, poll)

    def test_retry_clears_previous_job_id_and_uses_launch_token(self):
        dl_card = function_source(self.template, "dlCard")
        self.assertIn("jobId: null", dl_card)
        self.assertIn("launchToken", dl_card)
        self.assertIn("++downloadLaunchCounter", dl_card)

    def test_stale_catch_block_does_not_touch_new_card(self):
        dl_card = function_source(self.template, "dlCard")
        stale_return = dl_card.index("if (isStaleLaunch(card, index, launchToken)) return;")
        error_update = dl_card.index("card.status = 'error';")
        self.assertLess(stale_return, error_update)


if __name__ == "__main__":
    unittest.main()

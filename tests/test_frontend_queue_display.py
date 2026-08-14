import re
import shutil
import subprocess
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


class QueueDisplayFrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.template = read_template()

    def test_pollcard_reads_queue_position_and_keeps_queued_status(self):
        poll_card = function_source(self.template, "pollCard")
        self.assertIn("queuePosition: data.queue_position", poll_card)
        self.assertIn("data.status === 'queued' ? 'queued' : 'downloading'", poll_card)

    def test_active_statuses_include_queued(self):
        active = function_source(self.template, "isActiveStatus")
        for token in ("'downloading'", "'cancelling'", "'queued'"):
            with self.subTest(token=token):
                self.assertIn(token, active)

    def test_manifest_and_filter_count_queued_as_active(self):
        self.assertIn("isActiveStatus(card.status)", function_source(self.template, "updateManifest"))
        self.assertIn("isActiveStatus(card.status)", function_source(self.template, "statusMatchesFilter"))

    def test_dlcard_guard_blocks_queued_resubmission(self):
        self.assertIn(
            "if (!card || isActiveStatus(card.status)) return;",
            function_source(self.template, "dlCard"),
        )

    def test_settle_idle_waits_for_queued_jobs(self):
        self.assertIn("isActiveStatus(item.status)", function_source(self.template, "settleIdleActivity"))

    def test_queued_card_shows_position_without_fake_progress(self):
        parts = function_source(self.template, "progressParts")
        self.assertIn("Queued — #${position} in line", parts)
        self.assertIn("'Queued'", parts)
        self.assertIn("Waiting for a worker", parts)
        self.assertIn("hasPercent: false", parts)

    def test_queued_card_renders_active_with_cancel(self):
        render = function_source(self.template, "renderCard")
        self.assertIn("card.status === 'queued'", render)
        self.assertIn("(isDownloading || isQueued)", render)
        self.assertIn("data-cancel", render)
        cancel = function_source(self.template, "cancelCard")
        self.assertIn("!isActiveStatus(card.status)", cancel)

    def test_deferred_cancellation_marks_queued_cards_too(self):
        cancel_all = function_source(self.template, "cancelActiveDownloads")
        self.assertIn("card.cancelRequested = true", cancel_all)
        self.assertIn("if (!isActiveStatus(card.status)) return;", cancel_all)

    def test_cancelcard_restores_previous_status_on_delete_failure(self):
        cancel = function_source(self.template, "cancelCard")
        self.assertIn("const previousStatus = card.status;", cancel)
        self.assertIn("card.status = previousStatus;", cancel)
        # The restore must not be hardcoded to downloading.
        self.assertNotIn("card.status = 'downloading';", cancel)
        # previousStatus must be captured before the transition to cancelling.
        self.assertLess(
            cancel.index("const previousStatus = card.status;"),
            cancel.index("card.status = 'cancelling';"),
        )
        # The stale-card/state guard and the cancelRequested reset must survive.
        self.assertIn("cardData[index] === card && card.status === 'cancelling'", cancel)
        self.assertIn("card.cancelRequested = false;", cancel)
        # The failure branch must not touch the stored queue position.
        self.assertNotIn("queuePosition", cancel)


class CancelCardBehaviorTests(unittest.TestCase):
    """Executes the real cancelCard source in Node with stubbed collaborators.
    Skipped when Node is unavailable; no test framework or package is added."""

    def _run_cancel_card_harness(self):
        template = read_template()
        match = re.search(r"(async function cancelCard\(index\) \{.*?\n    \})", template, re.DOTALL)
        if match is None:
            raise AssertionError("cancelCard not found in template")
        harness = (
            "'use strict';\n"
            + match.group(1)
            + "\n"
            + "function isActiveStatus(status) { return status === 'downloading' || status === 'cancelling' || status === 'queued'; }\n"
            "function renderCard() {}\n"
            "function updateManifest() {}\n"
            "async function cancelJobOnServer() { return false; }\n"
            "let cardData;\n"
            "(async () => {\n"
            "  const queued = { status: 'queued', jobId: 'job1', queuePosition: 3 };\n"
            "  cardData = [queued];\n"
            "  await cancelCard(0);\n"
            "  if (queued.status !== 'queued') throw new Error('queued not restored: ' + queued.status);\n"
            "  if (queued.queuePosition !== 3) throw new Error('queuePosition lost: ' + queued.queuePosition);\n"
            "  if (queued.cancelRequested !== false) throw new Error('cancelRequested not reset');\n"
            "  const downloading = { status: 'downloading', jobId: 'job2' };\n"
            "  cardData = [downloading];\n"
            "  await cancelCard(0);\n"
            "  if (downloading.status !== 'downloading') throw new Error('downloading not restored: ' + downloading.status);\n"
            "  if (downloading.cancelRequested !== false) throw new Error('cancelRequested not reset');\n"
            "  console.log('HARNESS_OK');\n"
            "})().catch(error => { console.error(error.message); process.exit(1); });\n"
        )
        return subprocess.run(
            ["node", "-"],
            input=harness,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def test_delete_failure_restores_queued_and_downloading_states(self):
        if shutil.which("node") is None:
            self.skipTest("node is not available")
        result = self._run_cancel_card_harness()
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("HARNESS_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import app


ROOT = Path(app.__file__).parent


def read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def function_source(template, name):
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(.*?)\n    \}}",
        template,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"function {name} not found in template")
    return match.group(1)


class DockerYoutubeHardeningContracts(unittest.TestCase):
    def test_dockerfile_installs_pinned_deno_and_stays_non_root(self):
        dockerfile = read("Dockerfile")
        self.assertRegex(dockerfile, r"FROM denoland/deno:bin-2\.[3-9]\d*\.\d+ AS deno")
        self.assertIn("COPY --from=deno /deno /usr/local/bin/deno", dockerfile)
        self.assertIn("FROM python:3.12-slim AS base", dockerfile)
        self.assertIn("FROM base AS youtube-robust", dockerfile)
        self.assertIn("FROM base AS default", dockerfile)
        self.assertIn("USER linksift", dockerfile)
        base_part, robust_part = dockerfile.split("FROM base AS youtube-robust", 1)
        self.assertNotIn("bgutil", base_part)
        self.assertIn("requirements-youtube-robust.txt", robust_part)

    def test_requirements_bundle_ejs_and_pin_the_pot_plugin(self):
        self.assertIn("yt-dlp-ejs", read("requirements.txt"))
        robust = read("requirements-youtube-robust.txt")
        self.assertRegex(robust, r"bgutil-ytdlp-pot-provider==\d+\.\d+\.\d+")

    def test_entrypoint_updates_ytdlp_and_ejs_but_not_the_pot_plugin(self):
        entrypoint = read("docker-entrypoint.sh")
        self.assertIn("yt-dlp yt-dlp-ejs", entrypoint)
        self.assertIn("LINKSIFT_NO_UPDATE", entrypoint)
        self.assertNotIn("bgutil", entrypoint)
        self.assertNotIn(b"\r\n", (ROOT / "docker-entrypoint.sh").read_bytes())

    def test_robust_compose_override_contract(self):
        override = read("docker-compose.youtube-robust.yml")
        self.assertIn("target: youtube-robust", override)
        # The robust build must not overwrite the GPL-free linksift:latest tag.
        self.assertIn("image: linksift:youtube-robust", override)
        self.assertRegex(override, r"image: brainicism/bgutil-ytdlp-pot-provider:\d+\.\d+\.\d+")
        self.assertIn("condition: service_healthy", override)
        self.assertIn("LINKSIFT_PO_TOKEN_PROVIDER_URL", override)
        self.assertIn("healthcheck:", override)
        self.assertIn("expose:", override)
        # The provider must stay inside the Docker network only.
        self.assertNotIn("ports:", override)

    def test_pot_plugin_and_provider_sidecar_versions_match(self):
        plugin = re.search(r"bgutil-ytdlp-pot-provider==(\d+\.\d+\.\d+)", read("requirements-youtube-robust.txt"))
        sidecar = re.search(
            r"image: brainicism/bgutil-ytdlp-pot-provider:(\d+\.\d+\.\d+)",
            read("docker-compose.youtube-robust.yml"),
        )
        self.assertIsNotNone(plugin)
        self.assertIsNotNone(sidecar)
        self.assertEqual(plugin.group(1), sidecar.group(1))


class HealthCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_robust_mode_reports_configured_and_available(self):
        with patch.object(app.shutil, "which", return_value="/usr/bin/tool"), patch.object(
            app, "has_ejs_support", return_value=True
        ), patch.object(app, "has_pot_plugin", return_value=True), patch.dict(
            os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": "http://bgutil-provider:4416"}
        ):
            payload = self.client.get("/api/health").get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["missing_tools"], [])
        self.assertEqual(payload["capabilities"], {
            "youtube_js_runtime": True,
            "youtube_ejs": True,
            "po_token_provider_configured": True,
            "po_token_provider": True,
        })

    def test_valid_url_without_plugin_is_configured_but_not_available(self):
        with patch.object(app.shutil, "which", return_value="/usr/bin/tool"), patch.object(
            app, "has_ejs_support", return_value=True
        ), patch.object(app, "has_pot_plugin", return_value=False), patch.dict(
            os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": "http://bgutil-provider:4416"}
        ):
            payload = self.client.get("/api/health").get_json()
        self.assertTrue(payload["capabilities"]["po_token_provider_configured"])
        self.assertFalse(payload["capabilities"]["po_token_provider"])

    def test_invalid_provider_url_reports_both_fields_false(self):
        with patch.object(app.shutil, "which", return_value="/usr/bin/tool"), patch.object(
            app, "has_ejs_support", return_value=True
        ), patch.object(app, "has_pot_plugin", return_value=True), patch.dict(
            os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": "http://host;youtube:player_client=web"}
        ):
            payload = self.client.get("/api/health").get_json()
        self.assertFalse(payload["capabilities"]["po_token_provider_configured"])
        self.assertFalse(payload["capabilities"]["po_token_provider"])

    def test_missing_capabilities_are_not_fatal(self):
        with patch.object(app.shutil, "which", side_effect=lambda name: None if name == "deno" else "/usr/bin/tool"), patch.object(
            app, "has_ejs_support", return_value=False
        ), patch.object(app, "has_pot_plugin", return_value=False), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKSIFT_PO_TOKEN_PROVIDER_URL", None)
            payload = self.client.get("/api/health").get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["capabilities"], {
            "youtube_js_runtime": False,
            "youtube_ejs": False,
            "po_token_provider_configured": False,
            "po_token_provider": False,
        })

    def test_health_makes_no_network_calls(self):
        with patch("socket.socket", side_effect=AssertionError("health must not open sockets")), patch.dict(
            os.environ, {"LINKSIFT_PO_TOKEN_PROVIDER_URL": "http://bgutil-provider:4416"}
        ):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)


class RetryFrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.template = read("templates/index.html")

    def test_pollcard_reads_attempt_fields(self):
        poll_card = function_source(self.template, "pollCard")
        self.assertIn("attempt: data.attempt", poll_card)
        self.assertIn("maxAttempts: data.max_attempts", poll_card)

    def test_progress_parts_render_retrying_phase(self):
        parts = function_source(self.template, "progressParts")
        # Pin the exact condition so a mutated keying (e.g. also requiring a
        # queued status) cannot survive the text-presence checks.
        self.assertIn("const retrying = card.phase === 'retrying';", parts)
        self.assertIn("Retrying — attempt ${card.attempt} of ${card.maxAttempts}", parts)
        self.assertIn("'Retrying'", parts)
        self.assertIn("Waiting to retry", parts)
        # Retrying is keyed on phase, never on the queued status branch.
        self.assertLess(parts.index("card.status === 'queued'"), parts.index("card.phase === 'retrying'"))


if __name__ == "__main__":
    unittest.main()

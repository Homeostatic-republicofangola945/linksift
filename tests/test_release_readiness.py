import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


class ReleaseReadinessTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def assert_actions_are_pinned(self, workflow_path):
        workflow = self.read(workflow_path)
        action_refs = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        self.assertTrue(action_refs, f"No actions found in {workflow_path}")
        for action_ref in action_refs:
            with self.subTest(workflow=workflow_path, action=action_ref):
                self.assertRegex(action_ref, PINNED_ACTION)

    def test_ci_and_release_actions_are_commit_pinned(self):
        self.assert_actions_are_pinned(".github/workflows/ci.yml")
        self.assert_actions_are_pinned(".github/workflows/release.yml")

    def test_release_workflow_publishes_versioned_multi_arch_image(self):
        workflow = self.read(".github/workflows/release.yml")

        self.assertIn('"v[0-9]+.[0-9]+.[0-9]+"', workflow)
        self.assertIn("REGISTRY: ghcr.io", workflow)
        self.assertIn("IMAGE_NAME: ${{ github.repository }}", workflow)
        self.assertIn("platforms: linux/amd64,linux/arm64", workflow)
        self.assertIn("type=semver,pattern={{version}}", workflow)
        self.assertIn("type=semver,pattern={{major}},enable=${{ !startsWith(github.ref, 'refs/tags/v0.') }}", workflow)
        self.assertIn("type=raw,value=latest", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn('git merge-base --is-ancestor "$GITHUB_SHA" origin/main', workflow)
        self.assertIn("sbom: true", workflow)
        self.assertIn("push: true", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("contents: write", workflow)

    def test_release_workflow_attests_digest_before_creating_release(self):
        workflow = self.read(".github/workflows/release.yml")

        attest_position = workflow.index("- name: Attest image provenance")
        release_position = workflow.index("- name: Create GitHub release")
        self.assertLess(attest_position, release_position)
        self.assertIn("attestations: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("subject-digest: ${{ steps.push.outputs.digest }}", workflow)
        self.assertIn("push-to-registry: true", workflow)
        self.assertIn('gh release create "$GITHUB_REF_NAME"', workflow)
        self.assertIn("--verify-tag", workflow)

    def test_release_compose_uses_public_image_contract(self):
        compose = self.read("compose.ghcr.yml")

        self.assertIn("ghcr.io/loveisbl1nd/linksift:${LINKSIFT_VERSION:-latest}", compose)
        self.assertNotIn("build:", compose)
        self.assertIn('"127.0.0.1:8899:8899"', compose)
        self.assertIn("linksift-downloads:/app/downloads", compose)

    def test_container_has_oci_source_and_license_labels(self):
        dockerfile = self.read("Dockerfile")
        attributes = self.read(".gitattributes")
        entrypoint_bytes = (ROOT / "docker-entrypoint.sh").read_bytes()

        self.assertIn('org.opencontainers.image.source="https://github.com/loveisbl1nd/linksift"', dockerfile)
        self.assertIn('org.opencontainers.image.licenses="MIT"', dockerfile)
        self.assertIn("sed -i 's/\\r$//' /app/docker-entrypoint.sh", dockerfile)
        self.assertIn("*.sh text eol=lf", attributes)
        self.assertNotIn(b"\r\n", entrypoint_bytes)

        dockerignore = self.read(".dockerignore").splitlines()
        self.assertNotIn("LICENSE", dockerignore)
        self.assertNotIn("THIRD_PARTY_NOTICES.md", dockerignore)

    def test_provenance_records_exact_baseline_and_metrics_boundary(self):
        provenance = self.read("PROVENANCE.md")
        notices = self.read("THIRD_PARTY_NOTICES.md")
        license_text = self.read("LICENSE")
        baseline = "1d161d15a4fe93d9b3371377f0a421dc3e965b10"

        self.assertIn("https://github.com/averygan/reclip", provenance)
        self.assertIn(baseline, provenance)
        self.assertIn("does not claim activity or adoption belonging to the upstream repository", provenance)
        self.assertIn(baseline, notices)
        self.assertIn("Copyright (c) 2026\n", notices)
        self.assertIn("Copyright (c) 2026 iaht", license_text)

    def test_roadmap_has_contribution_paths_and_non_goals(self):
        roadmap = self.read("ROADMAP.md")

        for section in (
            "## Project principles",
            "## v0.1 — Reliable distribution",
            "## v0.2 — Diagnostics and compatibility",
            "## v0.3 — Maintainability and contributor experience",
            "## Non-goals",
            "## How to contribute to the roadmap",
        ):
            with self.subTest(section=section):
                self.assertIn(section, roadmap)

    def test_readme_links_release_provenance_and_roadmap_docs(self):
        readme = self.read("README.md")

        self.assertIn("ghcr.io/loveisbl1nd/linksift:latest", readme)
        self.assertIn("gh attestation verify oci://ghcr.io/loveisbl1nd/linksift:0.1.0", readme)
        for document in ("RELEASING.md", "PROVENANCE.md", "THIRD_PARTY_NOTICES.md", "ROADMAP.md"):
            with self.subTest(document=document):
                self.assertIn(f"]({document})", readme)


if __name__ == "__main__":
    unittest.main()

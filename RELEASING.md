# Releasing LinkSift

LinkSift releases are produced from immutable SemVer tags. The release workflow validates the source, publishes a multi-architecture image to GitHub Container Registry (GHCR), records an SBOM and build-provenance attestation, and creates the matching GitHub Release.

## One-time repository setup

1. In **Settings > Actions > General**, keep workflow permissions at the repository default; the release job requests only the scoped permissions it needs.
2. Keep tag creation restricted to maintainers and require CI on `main` through branch protection or a ruleset.
3. After the first image is published, open **Packages > linksift > Package settings** and change its visibility to **Public**. GHCR initially creates personal packages as private even when the source repository is public.
4. Confirm the package is connected to `loveisbl1nd/linksift`. The image's OCI source label normally creates this connection automatically.

No personal access token or repository secret is required. Publishing uses the short-lived `GITHUB_TOKEN` supplied to the workflow.

## Publish a release

1. Update [CHANGELOG.md](CHANGELOG.md): move completed entries from `Unreleased` into a versioned section with the release date.
2. Ensure the version follows `MAJOR.MINOR.PATCH` and that CI is green on the exact commit to release.
3. Create and push an annotated tag from `main`:

   ```bash
   git tag -a v0.2.0 -m "LinkSift v0.2.0"
   git push origin v0.2.0
   ```

4. Watch the **Release** workflow. It publishes these image tags for `v0.2.0`: `0.2.0`, `0.2`, and `latest`. Major-only tags begin with stable `v1.x` releases; the ambiguous `0` tag is intentionally omitted during initial development.
5. Confirm the generated GitHub Release notes and package visibility.

Do not move or overwrite a published tag. If a release is wrong, document it and publish a new patch version.

## Verify the published result

```bash
docker pull ghcr.io/loveisbl1nd/linksift:0.2.0
gh attestation verify oci://ghcr.io/loveisbl1nd/linksift:0.2.0 -R loveisbl1nd/linksift
docker run --rm ghcr.io/loveisbl1nd/linksift:0.2.0 yt-dlp --version
```

The attestation proves which GitHub repository, workflow, commit, and build identity produced the image digest. It does not replace review of the source, dependencies, or container contents.

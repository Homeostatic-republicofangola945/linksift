# Contributing to LinkSift

Thanks for contributing. LinkSift is a small Flask application with an offline unit-test suite; changes should remain focused, readable, and safe for local self-hosted use.

## Development setup

1. Install Python 3.12, yt-dlp, and ffmpeg.
2. Create and activate a virtual environment.
3. Install dependencies with `pip install -r requirements.txt`.
4. Run the app with `./linksift.sh` or `python app.py`.

## Docker smoke check

Before submitting a change that affects runtime packaging, run:

```bash
docker compose config
docker build -t linksift:local .
```

## Before opening a pull request

Run these checks locally:

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py
```

- Keep tests offline. Do not add CI calls to video platforms or other third-party services.
- Add regression coverage for behavior changes and bug fixes.
- Do not commit downloads, virtual environments, credentials, local logs, or generated audit artifacts.
- Preserve the one-worker deployment constraint unless the change also introduces shared job state.
- Update README/configuration documentation whenever user-visible behavior or environment variables change.

## Pull requests

Use a short, descriptive title. Explain the user-visible problem, approach, tests run, and any migration or security considerations. Keep unrelated formatting or refactoring out of the same pull request.

## Reporting security issues

Do not open public issues for security vulnerabilities. Follow [SECURITY.md](SECURITY.md).

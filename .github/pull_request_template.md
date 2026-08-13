## Summary

Describe the problem and the change.

## Validation

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m py_compile app.py`
- [ ] `docker compose config`
- [ ] Documentation updated if behavior/configuration changed

## Checklist

- [ ] Tests do not contact third-party media sites.
- [ ] No credentials, downloads, local logs, or generated artifacts are included.
- [ ] This change preserves the one-worker constraint or introduces shared job state.

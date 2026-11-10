# Contributing

- `pip install -r requirements-dev.txt && python -m pytest` must pass.
- `python scripts/dry_run.py` must produce a video with no network.
- New connector = one file in `connectors/`. New skill = folder in `skills/` with `SKILL.md`.
- Never commit secrets; never pin scraping libraries to old versions.

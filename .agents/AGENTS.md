# Workspace-Scoped Agent Rules (MLB-daily)

Single source of project rules:

- `CLAUDE.md` (repo root): workflow rules (start/stop work, approval gates). Claude Code loads it automatically; other tools must read it first.
- `docs/dev_rules.md`: detailed guidelines and report specs.

Rules:

1. Reply to the user in Traditional Chinese (Taiwan usage). Keep code, commands, file names and error messages in their original form.
2. Never commit API keys, webhook URLs or other secrets (`GEMINI_API_KEY`, `DISCORD_WEBHOOK_URL`, etc.). They live only in GitHub Secrets or local environment variables.
3. Any code change (`fetch_mlb.py`, `mlb_daily/`, rendering logic) must ship with unit tests, and `python -m unittest discover -s tests` must pass.
4. Do not break the daily pipeline (generate, deploy to `gh-pages`, wait for Pages, then notify). Read `docs/dev_rules.md` before touching it.
5. When the user says "收工", write a development log following `docs/dev_logs/README.md`.

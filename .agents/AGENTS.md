# Workspace-Scoped Agent Rules

As the coding agent for the `MLB-daily` project, you must strictly follow these workspace-scoped rules:

1. **Check Developer Rules First**:
   - Before starting any task or taking any action in this workspace, you MUST read [docs/dev_rules.md](../docs/dev_rules.md) and align on its constraints. Also check [docs/progress.md](../docs/progress.md) for the current status, and [docs/architecture.md](../docs/architecture.md) when touching code structure.

2. **Plan First, One Module at a Time**:
   - Discuss and confirm the plan with the user before changing code. Implement one module or feature at a time, and verify it before moving to the next.
   - UI changes follow the UI-first principle: show a static mockup (see `docs/mockups/`) and get user approval before wiring it to data.

3. **Tests Are Required**:
   - Any change to `fetch_mlb.py` or rendering logic must come with unit tests in `tests/`, and `python -m unittest discover -s tests` must pass with all tests OK.
   - Tests use only the standard library (`unittest`) and fixtures under `tests/fixtures/`. They must not touch the network, the real Gemini API, or Discord.

4. **No Secrets in the Repository**:
   - Never write API keys, webhook URLs, or tokens (`GEMINI_API_KEY`, `DISCORD_WEBHOOK_URL`, etc.) into code, docs, tests, or logs. They live only in GitHub Secrets or local environment variables.

5. **Keep the Pipeline Behavior Intact**:
   - The scheduled workflow generates the report only after all games are finished (waiting 30 minutes between checks, at most 8 checks), deploys to `gh-pages`, waits for Pages to be reachable, and only then sends the Discord notification. See `docs/dev_rules.md` rule 4 and 6 before altering any of this. Use only the Python standard library.

6. **Start Work ("開工") Command Rule**:
   - When the user says "開工" (Start work) in any session:
     1. Immediately read `docs/dev_rules.md` and `docs/progress.md`.
     2. Report the checklist status and outline the planned work for the current session.
     3. Explicitly ask for user approval and wait for confirmation before doing any code modifications.

7. **Stop Work ("收工") Command Rule**:
   - When the user says "收工" (Stop work) in any session:
     1. Summarize all changes made during the session.
     2. Output a ready-to-use Git commit message (containing a summary and description).
     3. Create a Markdown developer log under `docs/dev_logs/` named `YYYYMMDD_title.md` summarizing the modifications.

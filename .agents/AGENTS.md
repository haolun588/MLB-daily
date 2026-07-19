# Workspace-Scoped Agent Rules

As the coding agent for the `MLB-daily` project, you must strictly follow these workspace-scoped rules:

1. **Check Developer Rules First**:
   - Before starting any task or taking any action in this workspace, you MUST read the file `docs/dev_rules.md` (located at [dev_rules.md](file:///c:/GitHub/MLB-daily/docs/dev_rules.md)) to align on development constraints.

2. **No Code Generation Yet**:
   - Do NOT write or modify any code files (Python, HTML, CSS, JavaScript, Yaml) until the user explicitly requests you to begin Phase 2. Currently, only text/documentation changes are allowed.

3. **Step-by-Step UI Review**:
   - When requested to start Phase 2, build ONLY the static HTML/CSS files (`index.html` and `templates/report_template.html`). Do not implement the Python fetcher or GitHub Actions workflows yet. The user must manually preview the static files in their browser and approve the visual layout first.

4. **12:00 PM Taipei Time Check & Wait Rule**:
   - Remember that the automated system must check for all game results to be finalized (Final or Postponed) before starting the daily work. If not finalized, wait 30 minutes and re-check. Keep this architecture in mind during code planning.

5. **Start Work ("開工") Command Rule**:
   - When the user says "開工" (Start work) in any session:
     1. Immediately read `docs/dev_rules.md` and `docs/progress.md`.
     2. Report the checklist status and outline the planned work for the current session.
     3. Explicitly ask for user approval and wait for confirmation before doing any code modifications.

6. **Stop Work ("收工") Command Rule**:
   - When the user says "收工" (Stop work) in any session:
     1. Summarize all changes made during the session.
     2. Output a ready-to-use Git commit message (containing a summary and description).
     3. Create a Markdown developer log under `docs/dev_logs/` named `YYYYMMDD_title.md` summarizing the modifications.


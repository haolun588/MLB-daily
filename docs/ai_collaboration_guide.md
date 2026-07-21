# SYSTEM INSTRUCTION: Developer Agent Workspace Rules & Collaboration Workflow

This document serves as the system ruleset for the AI Developer Agent. The Agent MUST parse, internalize, and strictly adhere to the instructions defined herein.

---

## 1. Work Session Lifecycle (Session Commands)

The Agent's behavior is driven by two specific session command keywords: "開工" (Start Session) and "收工" (End Session).

### A. "開工" (Start Session)
When the User inputs "開工" (or any variations of "Start Work"), the Agent MUST execute the following sequence:
1. **Read Guidelines**: Read `docs/dev_rules.md` (Project Guidelines) and `docs/progress.md` (Project Progress Checklist) immediately.
2. **State Progress**: Output a brief summary of completed tasks and identify the current active/pending task.
3. **Present Session Plan**: Outline the specific files to be created/modified and the logic to be implemented during this session.
4. **Request Confirmation**: Ask the User for approval to begin. The Agent MUST NOT perform any code writes or modifications until the User explicitly grants permission.

### B. "收工" (End Session)
When the User inputs "收工" (or any variations of "Stop Work"), the Agent MUST execute the following sequence:
1. **Summarize Changes**: Provide a bulleted list of all changes made during the session (files modified, added, or deleted).
2. **Generate Commit Message**: Output a ready-to-use Git commit message containing a concise `Summary` and a detailed `Description` of the changes.
3. **Create Developer Log**: Write a new Markdown file to the `docs/dev_logs/` directory.
   - **Filename Format**: `YYYYMMDD_<short_title>.md` (e.g., `20260721_optimize_team_logos.md`).
   - **Log Content**: List added/modified/deleted files, summary of changes, and any outstanding items or issues.

---

## 2. Three-Phase Development Workflow

The Agent MUST complete tasks sequentially through three distinct phases. Moving to the next phase without explicit completion of the prior phase is strictly forbidden.

### Phase 1: Documentation & Specification
Before writing any code (scripts, backend logic, configurations):
1. **Create Docs Directory**: Ensure a `/docs` folder exists in the project root.
2. **Establish Specification Files**:
   - `docs/progress.md`: A markdown list of tasks using standard checklist boxes `- [ ]` and `- [x]`.
   - `docs/dev_rules.md`: Technical constraints, design parameters, API requirements, and domain logic guidelines.
   - `docs/architecture.md`: High-level explanation of components, data flow, and directories.

### Phase 2: UI-First Design (Static Mockup)
Before writing backend logic, automation scripts, or data fetchers:
1. **Build Static Visuals**: Develop the complete HTML and CSS structure (using modern, premium, dark/light mode designs).
2. **No Placeholders**: Use realistic mock data instead of raw placeholder text.
3. **Visual Verification**: Direct the User to preview the static files in their browser. The Agent MUST obtain the User's visual approval before writing any Python, API, database, or backend integration code.

### Phase 3: Logic Implementation & Automation
Once the static UI is approved:
1. **Modular Code Writes**: Implement backend fetchers, databases, APIs, or scripts one module at a time.
2. **Minimal Dependencies**: Prioritize native standard libraries to keep deployment lightweight and reduce CI/CD runner errors.
3. **Resilience & Fail-safe Logic**:
   - **Data Finalization Checks**: If APIs or data sources are not fully finalized, implement wait-and-retry logic (e.g., sleeping 30 minutes before re-checking).
   - **Off-season / Dry-run Handling**: If no data is available (e.g., zero tasks/games scheduled), the script must exit successfully (Code 0) without committing changes or triggering notifications.

---

## 3. General Agent Coding Constraints
- **Preserve Comments**: Do NOT remove or modify unrelated comments, headers, or docstrings in existing files unless requested.
- **Error Handling**: Wrap external API requests, database queries, and filesystem operations in proper try-catch/try-except blocks with timestamps.
- **Incremental Refactoring**: Do not attempt large-scale codebase refactoring without presenting an implementation plan and receiving User approval.

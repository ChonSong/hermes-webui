# Agent instructions for Hermes WebUI

## Dev fork

This is `ChonSong/hermes-webui` (fork of `nesquena/hermes-webui`) for developing features before upstream PRs. Live at `dev.codeovertcp.com`.

**Branch:** `dev` — all Player work lands here, then get PR'd to upstream.

**Stack:** Python server (no Docker in dev), JS/CSS frontend in `static/`, API routes in `api/`.

**Dev instance:** systemd service `hermes-webui-dev` on port 8788, serving from this worktree. After making changes, restart with:

This file is the shared entry point for AI assistants working in this
repository. Keep it project-specific and safe to publish. Do not put personal
machine setup, private network details, credentials, tokens, or local-only
workflow notes here.

## Read first

Before making changes, read:

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/CONTRACTS.md`
4. `CHANGELOG.md`

For architecture, testing, or setup work, also read the matching reference:

- `ARCHITECTURE.md` for design constraints and current module layout
- `TESTING.md` for local verification commands and manual test guidance
- `docs/onboarding.md` for first-run onboarding behavior
- `docs/troubleshooting.md` for diagnostic flows
- `docs/rfcs/README.md` for larger RFCs and state/durability contracts

For UI or UX work, read `docs/UIUX-GUIDE.md` and `DESIGN.md` before
changing layout, interaction flow, themes, chat rendering, or composer chrome.

## Onboarding and reinstall support

If the task involves install, reinstall, bootstrap, first-run onboarding,
provider setup, local model server setup, Docker onboarding, WSL onboarding, or
support for a failed first run, read `docs/onboarding-agent-checklist.md`
before running commands or inspecting logs.

Follow that checklist's safety rules:

- use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` for trials unless the
  human explicitly asks to use real state
- do not delete or overwrite a real `~/.hermes` directory without explicit
  approval
- do not print API keys, OAuth tokens, cookies, full `.env` files, full
  `auth.json` files, or password hashes
- collect non-secret status and log evidence before recommending a fix

## Contribution style

- Keep one logical change per PR; split unrelated refactors or cleanup.
- Read `docs/CONTRACTS.md` and the linked contract/RFC for the touched
  subsystem before editing.
- For local pytest runs, use `./scripts/test.sh` instead of bare `python3`,
  `python -m pytest`, or `pytest`. The script creates/uses the repo `.venv`,
  pins execution to Python 3.11-3.13, and installs missing dev test dependencies.
  `HERMES_WEBUI_TEST_PYTHON` selects the supported base interpreter used to
  create or rebuild `.venv`; it must not install test dependencies into a
  system/Homebrew interpreter directly.
  If a direct pytest invocation reports an unsupported interpreter, rerun through
  `./scripts/test.sh` before debugging product code.
- Prefer the existing Python + vanilla JavaScript structure. Do not add
  dependencies, build tools, frameworks, or long-lived processes without clear
  justification and a rollback story.
- Update docs when changing setup, onboarding, runtime behavior, architecture,
  testing guidance, or user-facing workflows.
- Do not edit `CHANGELOG.md` in ordinary contributor PRs. The release workflow
  owns changelog updates through release commits. If a change is release-note
  worthy, include concise release-note wording in the PR body instead.
- For UI or UX changes, include before/after evidence and test relevant
  desktop, narrow, and mobile states.
- For behavior changes, add or update automated tests where practical and list
  the manual verification performed.
- For runtime, streaming, recovery, replay, compression, or sidebar metadata
  changes, name the state layer being mutated and prove the relevant invariant.

## Local state and secrets

Hermes WebUI can read and write real agent state, sessions, workspaces,
credentials, and cron data. Treat local validation as potentially destructive
unless you have confirmed the active state directories.

Prefer isolated trial state for experiments:

```bash
HERMES_HOME=/tmp/hermes-webui-agent-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-agent-state \
HERMES_WEBUI_PORT=8789 \
python3 bootstrap.py
```

```bash
systemctl --user restart hermes-webui-dev
```

**Upstream:** `nesquena/hermes-webui` (add as remote: `git remote add upstream https://github.com/nesquena/hermes-webui.git`)

## Architecture

| Path | Purpose |
|------|---------|
| `server.py` | Entry point — HTTPServer + routing |
| `api/` | Backend API routes and business logic |
| `api/config.py` | Shared config, PORT via `HERMES_WEBUI_PORT` env |
| `static/` | Frontend JS, CSS, HTML |
| `static/sessions.js` | Session management, newSession() |
| `static/panels.js` | Sidebar project filter list |
| `static/tiles.js` | Tiling chat interface (PR #3861, need bug fixes) |
| `static/boot.js` | Bootstrap + keyboard shortcuts |
| `static/index.html` | Main HTML shell |
| `static/style.css` | All styles |

## Task Backlog

### task001 — Fix PR #3861 tiling bugs (EFFORT: 1 tick)

The PR at `nesquena/hermes-webui#3861` has 4 confirmed bugs from greptile review:

1. **display:grid vs flex** — `.tile-grid:not(.tile-grid--empty)` uses `display:flex` but sets CSS Grid properties. Fix: change to `display:grid` in `static/style.css`.
2. **Polling never clears on tile switch** — `_tileSend` polls using `S.session.session_id === tile.sid` condition that breaks when focus switches to another tile. Fix: poll against tile's own state, not global S.
3. **Stream cancel silently no-ops** — `closeTile()` calls `cancelSessionStream(tile.session)` but `session.active_stream_id` is never set. Fix: write `tile.activeStreamId` back to `tile.session.active_stream_id` in `_tileSend`.
4. **Grid layout for 3/5/6 tiles** — `_refreshGrid()` only handles 1, 2, 4 tiles. Fix: add proper layout for 3 tiles (2-col with last row centered or 1-col) and 5-6 tiles (3-col).
5. **API fetch lacks try/catch** — tiling intercept in `sessions.js` has unhandled rejection.

**Acceptance:** After fix, open dev.codeovertcp.com, enable tiling mode, open 3-6 sessions — all tiles render correctly. Switching tiles doesn't break streaming. Closing a busy tile cancels the stream. API failures show a toast.

### task002 — Project-specific "New conversation" buttons (EFFORT: 1-2 ticks)

Implements `nesquena/hermes-webui#4676`.

Add a "+" button on each project in the sidebar filter list that creates a new conversation assigned to that project in one click.

**Code path:** `static/panels.js` renders the project list. `newSession({project_id})` in `static/sessions.js` already supports the param.

**Changes needed:**
1. `static/panels.js` — In project render path, add `<button class="project-new-session" data-project-id="${p.id}" title="New session in ${p.name}">+</button>` visible on hover
2. Wire click handler to `newSession({project_id: p.id})`

**Acceptance:** Clicking "+" on any project creates a new conversation in that project. Works regardless of current filter. Mobile-friendly touch targets.

### task003 — Begin persistent voice mode (EFFORT: 3-5 ticks)

Implements `nesquena/hermes-webui#4761`.

Persistent MediaRecorder loop → /api/transcribe → auto-send → agent response → TTS playback → loop.

**Frontend changes only** — all backend endpoints exist (`/api/transcribe`, `/api/chat`, TTS pipeline).

**Phase A (1 tick):** Voice chat toggle button + recording state machine
**Phase B (1-2 ticks):** MediaRecorder loop → /api/transcribe → send() pipeline
**Phase C (1 tick):** Response TTS playback → auto-re-arm mic
**Phase D (1 tick):** Visual indicators (listening/speaking/processing states)

## Development Notes

- **Branch strategy:** All Player work lands on `dev` branch. After Coach approves, PR from `ChonSong/hermes-webui:dev` to `nesquena/hermes-webui:master`.
- **Testing:** There's no formal test suite for the frontend. Verify changes by refreshing `dev.codeovertcp.com`.
- **Commit convention:** `feat(scope):`, `fix(scope):` — scope can be `tiles`, `projects`, `voice`, `ui`, `api`.
- **Coach verification:** Coach should browser_navigate to `dev.codeovertcp.com` and visually verify changes.
- **Don't modify AGENTS.md** except in Task Exhaustion Recovery.

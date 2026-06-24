# AGENTS.md — Hermes WebUI (Dev)

## About
Fork of `nesquena/hermes-webui` for developing features before upstream PRs. Live at `dev.codeovertcp.com`.

**Branch:** `dev` — all Player work lands here, then get PR'd to upstream.

**Stack:** Python server (no Docker in dev), JS/CSS frontend in `static/`, API routes in `api/`.

**Dev instance:** systemd service `hermes-webui-dev` on port 8788, serving from this worktree. After making changes, restart with:
```
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

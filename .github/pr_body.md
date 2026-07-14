## Summary

Implements the full user flow for saving a Hermes conversation as a wiki page, following these user principles:
- **Smart-default modal**: Pre-filled page name + section. Focus on "Save to Wiki" button → Enter saves. One-click fast.
- **High signal, low noise**: System messages stripped, tool calls folded into `<details><summary>` blocks.
- **Graceful 409 conflict**: Modal morphs to "Already exists" view with primary "Append to bottom" action (timestamped divider).

## Components

### Backend
- **`api/routes.py`**: `POST /api/wiki/page` route → `_handle_wiki_page_write(handler, body)`. Modes: `create` (201/409) and `append` (200/409). Validates `page_name` (safe slug), resolves wiki root, performs containment check.
- **`api/wiki_capture.py`**: `render_session_markdown(session)` → Markdown string with YAML frontmatter. Strips system messages, folds tool calls, supports multimodal content flattening.

### Frontend
- **`static/ui.js`**: `openWikiSaveModal(session, meta)` — smart-default modal. Auto-slugifies title, pre-selects section from extension settings. Conflict morphing on 409.
- **`static/boot.js`**: `Ctrl+Shift+W` keyboard shortcut when not composing.
- **`static/sessions.js`**: "Add to Wiki" menu item in per-session context menu (with `getWikiSessionMeta()` for extension settings integration).

## Tests
- `tests/test_wiki_page_put.py` (17 tests): backend + renderer
- `tests/test_wiki_session_menu.py` (9 tests): session menu action

## Security
- `page_name` validated: `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (no traversal, no dot-files)
- Containment check: target must resolve under wiki root
- No symlinks followed (reuse existing safety model from read path)

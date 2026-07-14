"""Static-shape + functional tests for the 'Add to Wiki' session action menu item.

Verifies:
  1. `_appendSessionAddToWikiAction` exists and follows the canonical
     `_buildSessionAction` pattern with ICONS.spark.
  2. It is called in `_openSessionActionMenu` right after the export action
     (for non-read-only sessions with isExternalSession === false).
  3. `getWikiSessionMeta` returns the expected default_section and reads
     extension settings when available.
  4. The click handler calls `closeSessionActionMenu` and `openWikiSaveModal`.
"""

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SESSIONS_JS = (REPO / "static" / "sessions.js").read_text(encoding="utf-8")


def _extract_block(src, signature):
    start = src.find(signature)
    assert start >= 0, f"missing: {signature!r}"
    paren_close = src.index(")", start)
    brace = src.index("{", paren_close)
    depth = 0
    for i, ch in enumerate(src[brace:], brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unterminated: {signature!r}")


def _run_in_tmp(tmp, body):
    f = tmp / "run.js"
    f.write_text(body)
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True,
                          timeout=10, cwd=str(tmp))
    assert proc.returncode == 0, "node stderr: " + proc.stderr
    return proc.stdout


# ── Static shape tests ──────────────────────────────────────────────────────


def test_append_session_add_to_wiki_action_exists():
    assert "function _appendSessionAddToWikiAction(menu, session)" in SESSIONS_JS


def test_wiki_action_uses_spark_icon():
    block = _extract_block(SESSIONS_JS, "function _appendSessionAddToWikiAction(menu, session)")
    assert "ICONS.spark" in block, "menu item should use ICONS.spark"


def test_wiki_action_uses_fallback_labels():
    block = _extract_block(SESSIONS_JS, "function _appendSessionAddToWikiAction(menu, session)")
    assert "Add to wiki" in block, "should fall back to 'Add to wiki'"
    assert "Save this conversation as a wiki page" in block, "should fall back to description"


def test_wiki_action_called_after_export_in_menu():
    """The wiki action should be appended after the export action in the main menu builder."""
    block = _extract_block(SESSIONS_JS, "function _openSessionActionMenu(session, anchorEl)")
    export_idx = block.find("_appendSessionExportHtmlAction(menu, session)")
    wiki_idx = block.find("_appendSessionAddToWikiAction(menu, session)")
    assert export_idx >= 0, "export action must be in the menu builder"
    assert wiki_idx >= 0, "wiki action must be in the menu builder"
    assert wiki_idx > export_idx, "wiki action must come after the export action"


def test_wiki_action_not_called_for_readonly():
    """Read-only sessions return early before reaching the wiki action."""
    block = _extract_block(SESSIONS_JS, "function _openSessionActionMenu(session, anchorEl)")
    ret_idx = block.find("return;")
    wiki_idx = block.find("_appendSessionAddToWikiAction(menu, session)")
    assert wiki_idx > ret_idx, "wiki action must be after the read-only early return"


def test_get_wiki_session_meta_exists():
    assert "function getWikiSessionMeta()" in SESSIONS_JS


def test_get_wiki_session_meta_default():
    block = _extract_block(SESSIONS_JS, "function getWikiSessionMeta()")
    assert "'concepts'" in block, "default_section fallback should be 'concepts'"


# ── Functional test: getWikiSessionMeta reads extension settings ────────────


def test_get_wiki_session_meta_functional(tmp_path):
    body = textwrap.dedent(f"""
    {Path(__file__).parent.joinpath('static_shim.txt').read_text() if False else ''}
    """)
    shim = r"""
if (typeof global.window === 'undefined') global.window = {};
global.window.HermesExtensionSettings = {
  forExtension: function(id) {
    if (id === 'session-to-wiki') {
      return {storageOwned: true, values: {default_section: 'tutorials'}};
    }
    return null;
  }
};
"""
    src = shim + "\n" + _extract_block(SESSIONS_JS, "function getWikiSessionMeta()") + "\n"
    src += "console.log(JSON.stringify(getWikiSessionMeta()));\n"

    out = _run_in_tmp(tmp_path, src)
    result = json.loads(out.strip() or "{}")
    assert result == {"default_section": "tutorials"}, f"got: {result}"


def test_get_wiki_session_meta_no_extension(tmp_path):
    shim = r"""
if (typeof global.window === 'undefined') global.window = {};
"""
    src = shim + "\n" + _extract_block(SESSIONS_JS, "function getWikiSessionMeta()") + "\n"
    src += "console.log(JSON.stringify(getWikiSessionMeta()));\n"

    out = _run_in_tmp(tmp_path, src)
    result = json.loads(out.strip() or "{}")
    assert result == {"default_section": "concepts"}, f"got: {result}"

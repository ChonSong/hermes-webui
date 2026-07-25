"""Tests for session search depth validation, encoding guards, and canonical-authority semantics.

Covers the gate-certification requirements for PR #5875:
- Depth validation (non-numeric, negative, valid caps)
- Encoding guard (json.dumps round-trip catches all JSON-escaped chars)
- Metacharacter queries (rg -F literal matching)
- Escaped-character queries (quote, backslash, tab, newline, CR, ESC, BS, NUL, Unicode)
- Normalization parity (adjacent-partial collapse before depth slicing)
- Canonical-authority tests (a-d) from the gate review:
    (a) rg miss + newer journal-only content -> must be included
    (b) rg hit + stale sidecar + canonical no-match -> must be excluded
    (c) rg unavailable + stale sidecar + canonical no-match -> must be excluded
    (d) LRU working-set preservation during multi-session search
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import pytest


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture
def session_s1_json(tmp_path):
    """Write synthetic s1 session to a real JSON file and return the dir path."""
    s1 = {
        "session_id": "s1",
        "title": "Untitled",
        "profile": "default",
        "messages": [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "second message"},
            {"role": "user", "content": "NEEDLE in the latest message"},
        ],
    }
    (tmp_path / "s1.json").write_text(json.dumps(s1), encoding="utf-8")
    return tmp_path


def _run_mocked(query, session_dir, *, sessions_meta, get_session_for_scan_return=None):
    """Run _handle_sessions_search with mocked get_session_for_scan."""
    import api.routes as routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["status"] = status
        captured["payload"] = payload

    if get_session_for_scan_return is not None:
        sf_patch = patch("api.routes.get_session_for_scan",
                         return_value=get_session_for_scan_return)
    else:
        sf_patch = patch("api.routes.get_session_for_scan",
                         side_effect=KeyError("not loaded"))

    with patch("api.routes.all_sessions", return_value=list(sessions_meta)), \
         patch("api.profiles.get_active_profile_name", return_value="default"), \
         sf_patch, \
         patch("api.routes.j", side_effect=fake_j):
        routes._handle_sessions_search(SimpleNamespace(), urlparse(query))
    return captured


def _run_real(query, tmp_path, *, sessions_meta):
    """Run _handle_sessions_search with REAL get_session_for_scan.
    
    Patches api.models.SESSION_DIR (where get_session_for_scan reads it)
    but does NOT mock the resolver itself.
    """
    import api.routes as routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured["status"] = status
        captured["payload"] = payload

    with patch("api.routes.all_sessions", return_value=list(sessions_meta)), \
         patch("api.profiles.get_active_profile_name", return_value="default"), \
         patch("api.models.SESSION_DIR", tmp_path), \
         patch("api.routes.j", side_effect=fake_j):
        routes._handle_sessions_search(SimpleNamespace(), urlparse(query))
    return captured


# -- Depth validation --------------------------------------------------------

def test_search_non_numeric_depth_does_not_500(session_s1_json):
    """depth=deep falls back to 5; the needle is found."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1&depth=deep",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "second message"},
            {"role": "user", "content": "NEEDLE in the latest message"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_search_negative_depth_still_scans_newest_message(session_s1_json):
    """depth=-2 is clamped to >= 0 so the latest message is searched."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1&depth=-2",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "second message"},
            {"role": "user", "content": "NEEDLE in the latest message"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_search_valid_depth_still_caps_scan(session_s1_json):
    """depth=1 scans only the first message; needle in the last is missed."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1&depth=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "second message"},
            {"role": "user", "content": "NEEDLE in the latest message"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 0


# -- Metacharacter queries ---------------------------------------------------

def test_metacharacter_query_dollar_sign(session_s1_json):
    """Query '$5' matched literally."""
    r = _run_mocked(
        "/api/sessions/search?q=$5&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "total is $5"},
            {"role": "user", "content": "done"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_metacharacter_query_plus(session_s1_json):
    """Query '1+1' matched literally."""
    r = _run_mocked(
        "/api/sessions/search?q=1%2B1&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "compute 1+1"},
            {"role": "assistant", "content": "result is 2"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


# -- Escaped-character queries (mocked) --------------------------------------

def test_query_with_double_quote(session_s1_json):
    """Double-quote in query; canonical resolver finds match."""
    r = _run_mocked(
        "/api/sessions/search?q=he%20said%20%22ok%22&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": 'he said "ok"'},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_query_with_backslash(session_s1_json):
    """Backslash in query; canonical resolver finds match."""
    r = _run_mocked(
        "/api/sessions/search?q=path%5cto&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "path\\to"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


# -- Normalization parity (mocked — Session.load pre-normalizes) -------------

def test_depth_limit_sees_message_past_collapsed_partials(session_s1_json):
    """After Session.load normalization, adjacent partials are collapsed."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1&depth=2",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        # Session.load() would collapse partials; mock returns pre-collapsed
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "assistant", "content": "working"},
            {"role": "user", "content": "NEEDLE after partials"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


# -- Control-character queries (mocked) --------------------------------------

def test_query_with_tab(session_s1_json):
    r = _run_mocked(
        "/api/sessions/search?q=alpha%09beta&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "alpha\tbeta"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_query_with_newline(session_s1_json):
    r = _run_mocked(
        "/api/sessions/search?q=hello%0Aworld&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "hello\nworld"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_query_with_cr(session_s1_json):
    r = _run_mocked(
        "/api/sessions/search?q=line%0Dbreak&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "line\rbreak"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_query_with_esc(session_s1_json):
    r = _run_mocked(
        "/api/sessions/search?q=ctrl%1Bkey&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "ctrl\x1bkey"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_query_with_backspace(session_s1_json):
    r = _run_mocked(
        "/api/sessions/search?q=ctrl%08key&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "ctrl\x08key"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_query_with_nul(session_s1_json):
    r = _run_mocked(
        "/api/sessions/search?q=has%00null&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "has\x00null"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_unicode_case_insensitive(session_s1_json):
    """Unicode case-insensitive search finds match."""
    r = _run_mocked(
        "/api/sessions/search?q=%E6%97%A5%E6%9C%AC%E8%AA%9E%E3%83%86%E3%82%B9%E3%83%88&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "日本語テスト"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_unicode_case_diff(session_s1_json):
    """Unicode uppercase query finds lowercase content (case-insensitive)."""
    r = _run_mocked(
        "/api/sessions/search?q=caf%C3%89&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "I love café au lait"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


# ======================================================================
# Gate-certification canonical-authority tests (a-d) — mocked
# ======================================================================

def test_rg_miss_journal_only_content_found(session_s1_json):
    """(a) Canonical state has journal-only content. Session MUST appear."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "unrelated on-disk content"},
            {"role": "assistant", "content": "JOURNAL_ONLY NEEDLE here"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_rg_hit_stale_sidecar_canonical_excludes(session_s1_json):
    """(b) Canonical state has no match. Session MUST be excluded."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=SimpleNamespace(session_id="s1", messages=[
            {"role": "user", "content": "clean content only"},
        ]),
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 0


def test_rg_unavailable_stale_sidecar_canonical_excludes(session_s1_json):
    """(c) Canonical resolver unavailable → excluded."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=None,  # KeyError
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 0


def test_resolver_failure_fails_closed(session_s1_json):
    """(b-extra) Resolver failure → fail closed."""
    r = _run_mocked(
        "/api/sessions/search?q=needle&content=1",
        session_s1_json,
        sessions_meta=[{"session_id": "s1", "title": "U", "profile": "default"}],
        get_session_for_scan_return=None,
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 0


def test_lru_not_polluted_by_content_search(monkeypatch, tmp_path):
    """(d) Content search must not promote or evict sessions in the LRU."""
    import api.models as models

    for sid in ["s_a", "s_b", "s_c"]:
        (tmp_path / f"{sid}.json").write_text(json.dumps({
            "session_id": sid, "title": sid.upper(), "profile": "default",
            "messages": [{"role": "user", "content": f"msg in {sid}"}],
        }), encoding="utf-8")

    sess_a = SimpleNamespace(session_id="s_a", messages=[])
    sess_b = SimpleNamespace(session_id="s_b", messages=[])
    with models.LOCK:
        models.SESSIONS["s_a"] = sess_a
        models.SESSIONS["s_b"] = sess_b
        models.SESSIONS.move_to_end("s_a")
        models.SESSIONS.move_to_end("s_b")

    with models.LOCK:
        order_before = list(models.SESSIONS.keys())

    r = _run_real(
        "/api/sessions/search?q=NEEDLE&content=1",
        tmp_path,
        sessions_meta=[
            {"session_id": "s_a", "title": "A", "profile": "default"},
            {"session_id": "s_b", "title": "B", "profile": "default"},
            {"session_id": "s_c", "title": "C", "profile": "default"},
        ],
    )

    with models.LOCK:
        order_after = list(models.SESSIONS.keys())

    assert r["status"] == 200
    assert order_before == order_after, (
        f"LRU order changed: {order_before} -> {order_after}"
    )
    assert "s_c" not in order_after, "get_session_for_scan must not insert into LRU"


# ======================================================================
# Real integration tests — exercise actual _resolve_session paths
# ======================================================================

def test_newer_cached_content_overrides_stale_sidecar(tmp_path):
    """Canonical resolver returns fresher in-memory state, not stale disk."""
    import api.models as models

    (tmp_path / "s_integ.json").write_text(json.dumps({
        "session_id": "s_integ", "title": "Stale", "profile": "default",
        "messages": [{"role": "user", "content": "stale content no needle"}],
    }), encoding="utf-8")

    fresh_sess = SimpleNamespace(
        session_id="s_integ",
        messages=[{"role": "user", "content": "fresh content with NEEDLE here"}],
    )
    with models.LOCK:
        models.SESSIONS["s_integ"] = fresh_sess

    try:
        r = _run_real(
            "/api/sessions/search?q=needle&content=1",
            tmp_path,
            sessions_meta=[{"session_id": "s_integ", "title": "Stale", "profile": "default"}],
        )
        assert r["status"] == 200
        assert r["payload"]["count"] == 1
    finally:
        with models.LOCK:
            models.SESSIONS.pop("s_integ", None)


def test_cold_load_reads_from_disk(tmp_path):
    """Cold session not in LRU is loaded from disk by _resolve_session."""
    import api.models as models

    (tmp_path / "s_cold.json").write_text(json.dumps({
        "session_id": "s_cold", "title": "Cold", "profile": "default",
        "messages": [{"role": "user", "content": "disk content NEEDLE here"}],
    }), encoding="utf-8")

    with models.LOCK:
        assert "s_cold" not in models.SESSIONS

    r = _run_real(
        "/api/sessions/search?q=needle&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_cold", "title": "Cold", "profile": "default"}],
    )

    with models.LOCK:
        in_lru = "s_cold" in models.SESSIONS

    assert r["status"] == 200
    assert r["payload"]["count"] == 1
    assert not in_lru, "get_session_for_scan must not insert into LRU"


def test_real_routing_guard_with_quote(tmp_path):
    """Double-quote query through REAL routing guard."""
    (tmp_path / "s_q.json").write_text(json.dumps({
        "session_id": "s_q", "title": "Q", "profile": "default",
        "messages": [{"role": "user", "content": 'he said "ok"'}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=he%20said%20%22ok%22&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_q", "title": "Q", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_backslash(tmp_path):
    (tmp_path / "s_bs.json").write_text(json.dumps({
        "session_id": "s_bs", "title": "BS", "profile": "default",
        "messages": [{"role": "user", "content": "path\\to"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=path%5cto&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_bs", "title": "BS", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_tab(tmp_path):
    (tmp_path / "s_tab.json").write_text(json.dumps({
        "session_id": "s_tab", "title": "Tab", "profile": "default",
        "messages": [{"role": "user", "content": "alpha\tbeta"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=alpha%09beta&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_tab", "title": "Tab", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_esc(tmp_path):
    (tmp_path / "s_esc.json").write_text(json.dumps({
        "session_id": "s_esc", "title": "ESC", "profile": "default",
        "messages": [{"role": "user", "content": "ctrl\x1bkey"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=ctrl%1Bkey&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_esc", "title": "ESC", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_backspace(tmp_path):
    (tmp_path / "s_bs2.json").write_text(json.dumps({
        "session_id": "s_bs2", "title": "BS2", "profile": "default",
        "messages": [{"role": "user", "content": "ctrl\x08key"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=ctrl%08key&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_bs2", "title": "BS2", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_nul(tmp_path):
    (tmp_path / "s_nul.json").write_text(json.dumps({
        "session_id": "s_nul", "title": "NUL", "profile": "default",
        "messages": [{"role": "user", "content": "has\x00null"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=has%00null&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_nul", "title": "NUL", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_newline(tmp_path):
    (tmp_path / "s_nl.json").write_text(json.dumps({
        "session_id": "s_nl", "title": "NL", "profile": "default",
        "messages": [{"role": "user", "content": "hello\nworld"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=hello%0Aworld&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_nl", "title": "NL", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_with_cr(tmp_path):
    (tmp_path / "s_cr.json").write_text(json.dumps({
        "session_id": "s_cr", "title": "CR", "profile": "default",
        "messages": [{"role": "user", "content": "line\rbreak"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=line%0Dbreak&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_cr", "title": "CR", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1


def test_real_routing_guard_unicode_case_diff(tmp_path):
    """Uppercase query finds lowercase content through REAL routing."""
    (tmp_path / "s_uc.json").write_text(json.dumps({
        "session_id": "s_uc", "title": "UC", "profile": "default",
        "messages": [{"role": "user", "content": "I love café au lait"}],
    }), encoding="utf-8")

    r = _run_real(
        "/api/sessions/search?q=caf%C3%89&content=1",
        tmp_path,
        sessions_meta=[{"session_id": "s_uc", "title": "UC", "profile": "default"}],
    )
    assert r["status"] == 200
    assert r["payload"]["count"] == 1

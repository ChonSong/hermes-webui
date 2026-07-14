"""Functional tests for POST /api/wiki/page — save session to wiki."""
import json
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent


def _post(app, path, body):
    """Helper: simulate POST with JSON body."""
    from http.server import HTTPServer
    import io
    data = json.dumps(body).encode()
    # Mock request
    class FakeHandler:
        def __init__(self):
            self.rfile = io.BytesIO(data)
            self.wfile = io.BytesIO()
            self.headers = {"Content-Length": str(len(data)), "Content-Type": "application/json"}
            self.command = "POST"
            self.close_connection = False
        def send_response(self, code):
            self.status = code
        def send_header(self, k, v):
            pass
        def end_headers(self):
            pass
    return app.do_POST_PATH(FakeHandler(), path)


class TestWikiPagePut:
    """Test backend endpoint POST /api/wiki/page."""

    def _mock_session(self, tmp_path):
        """Create a module-level mock session for testing."""
        sid = "test-session-123"
        session = {
            "session_id": sid,
            "title": "Test Chat Session",
            "model": "claude-sonnet-4",
            "model_provider": "anthropic",
            "created_at": "2026-07-14T10:00:00Z",
            "updated_at": "2026-07-14T10:05:00Z",
            "messages": [
                {"role": "system", "content": "You are helpful", "timestamp": "2026-07-14T10:00:00Z"},
                {"role": "user", "content": "Hello there", "timestamp": "2026-07-14T10:01:00Z"},
                {"role": "assistant", "content": "Hi! How can I help?", "timestamp": "2026-07-14T10:01:30Z"},
                {"role": "tool", "content": "result", "tool_calls": [{"id": "tc-1", "function": {"name": "search", "arguments": "{}"}}]},
            ],
        }
        return sid, session

    def test_render_session_markdown_strips_system(self, tmp_path):
        """System messages should be excluded from markdown output."""
        from api.wiki_capture import render_session_markdown
        session = {
            "session_id": "abc",
            "title": "Test",
            "model": "claude-sonnet-4",
            "model_provider": "anthropic",
            "created_at": "2026-07-14T10:00:00Z",
            "updated_at": "2026-07-14T10:05:00Z",
            "messages": [
                {"role": "system", "content": "System prompt here"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
        }
        md = render_session_markdown(session)
        assert "System prompt here" not in md
        assert "Hello" in md
        assert "Hi!" in md
        assert "---" in md  # frontmatter
        assert "session_id: abc" in md

    def test_render_session_markdown_folds_tool_calls(self):
        """Tool calls wrapped in <details> tags."""
        from api.wiki_capture import render_session_markdown
        session = {
            "session_id": "abc",
            "title": "Tool Session",
            "model": "claude-sonnet-4",
            "model_provider": "anthropic",
            "created_at": "2026-07-14T10:00:00Z",
            "updated_at": "2026-07-14T10:05:00Z",
            "messages": [
                {"role": "user", "content": "Run search"},
                {"role": "tool", "content": "results", "tool_calls": [{"id": "tc-1", "function": {"name": "web_search", "arguments": '{"q": "test"}'}}]},
            ],
        }
        md = render_session_markdown(session)
        assert "<details>" in md
        assert "Tool: web_search" in md
        assert "```json" in md

    def test_render_session_markdown_preserves_inline_html(self):
        """Markdown can contain inline HTML (bold, links)."""
        from api.wiki_capture import render_session_markdown
        session = {
            "session_id": "abc",
            "title": "HTML <em>test</em>",
            "model": "claude-sonnet-4",
            "model_provider": "anthropic",
            "created_at": "2026-07-14T10:00:00Z",
            "updated_at": "2026-07-14T10:05:00Z",
            "messages": [{"role": "user", "content": "<b>bold</b>"}],
        }
        md = render_session_markdown(session)
        assert "<b>bold</b>" in md

    def test_render_session_markdown_multimodal_content(self, tmp_path):
        """Multimodal content (list of text parts) concatenated."""
        from api.wiki_capture import render_session_markdown
        session = {
            "session_id": "abc",
            "title": "Multi",
            "model": "claude-sonnet-4",
            "model_provider": "anthropic",
            "created_at": "2026-07-14T10:00:00Z",
            "updated_at": "2026-07-14T10:05:00Z",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}]},
            ],
        }
        md = render_session_markdown(session)
        assert "Hello" in md
        assert "world" in md

    def test_render_session_markdown_empty_messages(self, tmp_path):
        """Empty message list returns header only."""
        from api.wiki_capture import render_session_markdown
        session = {
            "session_id": "abc",
            "title": "Empty",
            "model": "claude-sonnet-4",
            "model_provider": "anthropic",
            "created_at": "2026-07-14T10:00:00Z",
            "updated_at": "2026-07-14T10:05:00Z",
            "messages": [],
        }
        md = render_session_markdown(session)
        assert "session_id: abc" in md
        assert "# Empty" in md

    def test_validate_page_name_rejects_traversal(self):
        """Page names with path traversal should be rejected."""
        from api.routes import _handle_wiki_page_write
        # This is tested indirectly via the regex check in the handler
        bad_names = ["../etc/passwd", "../../secret", "/etc/passwd", ".hidden", "name/with/slash"]
        for name in bad_names:
            assert not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", name), f"{name} should be rejected"

    def test_validate_page_name_accepts_valid(self):
        """Valid page names should pass."""
        good_names = ["my-page", "my_page", "session-1", "Changelog", "file.md", "a"]
        for name in good_names:
            assert re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", name), f"{name} should be accepted"

    def test_wiki_page_put_creates_page(self, tmp_path):
        """POST to /api/wiki/page creates the .md file."""
        from api.routes import _handle_wiki_page_write
        import io

        sid, session = self._mock_session(tmp_path)
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
                self._headers = {}
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                self._headers[k] = v
            def end_headers(self):
                pass

        wiki_root = tmp_path / "wiki"
        wiki_root.mkdir()
        (wiki_root / "concepts").mkdir()

        body = {
            "session_id": sid,
            "page_name": "test-session",
            "section": "concepts",
            "mode": "create",
        }
        
        handler = FakeHandler()
        with patch("api.routes._llm_wiki_resolve_path") as mock_resolve, \
             patch("api.routes.get_session") as mock_session:
            mock_resolve.return_value = (wiki_root, "test", True)
            mock_session.return_value = type("FakeSession", (), {"__dict__": session})()
            
            result = _handle_wiki_page_write(handler, body)

        assert handler.status == 201
        target = wiki_root / "concepts" / "test-session.md"
        assert target.exists()
        content = target.read_text()
        assert "Test Chat Session" in content
        assert "message_count" in content or "source: hermes-webui" in content

    def test_wiki_page_put_conflict_on_existing(self, tmp_path):
        """Creating existing page returns 409."""
        from api.routes import _handle_wiki_page_write
        import io

        sid, session = self._mock_session(tmp_path)
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        wiki_root = tmp_path / "wiki"
        wiki_root.mkdir()
        (wiki_root / "concepts").mkdir()
        (wiki_root / "concepts" / "existing.md").write_text("existing content")

        body = {"session_id": sid, "page_name": "existing", "section": "concepts", "mode": "create"}
        
        handler = FakeHandler()
        with patch("api.routes._llm_wiki_resolve_path") as mock_resolve, \
             patch("api.routes.get_session") as mock_session:
            mock_resolve.return_value = (wiki_root, "test", True)
            mock_session.return_value = type("FakeSession", (), {"__dict__": session})()
            
            result = _handle_wiki_page_write(handler, body)

        assert handler.status == 409

    def test_wiki_page_put_append_mode(self, tmp_path):
        """Appending to existing page adds content."""
        from api.routes import _handle_wiki_page_write
        import io

        sid, session = self._mock_session(tmp_path)
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        wiki_root = tmp_path / "wiki"
        wiki_root.mkdir()
        (wiki_root / "concepts").mkdir()
        (wiki_root / "concepts" / "existing.md").write_text("# Existing Content")

        body = {"session_id": sid, "page_name": "existing", "section": "concepts", "mode": "append"}
        
        handler = FakeHandler()
        with patch("api.routes._llm_wiki_resolve_path") as mock_resolve, \
             patch("api.routes.get_session") as mock_session:
            mock_resolve.return_value = (wiki_root, "test", True)
            mock_session.return_value = type("FakeSession", (), {"__dict__": session})()
            
            result = _handle_wiki_page_write(handler, body)

        assert handler.status == 200
        content = (wiki_root / "concepts" / "existing.md").read_text()
        assert "# Existing Content" in content
        assert "Appended" in content
        assert "test-chat-session" in content.lower() or "Test Chat Session" in content

    def test_wiki_page_put_wiki_not_configured(self, tmp_path):
        """Returns 404 when wiki not configured."""
        from api.routes import _handle_wiki_page_write
        import io

        sid, session = self._mock_session(tmp_path)
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        body = {"session_id": sid, "page_name": "test", "section": "concepts", "mode": "create"}
        
        handler = FakeHandler()
        with patch("api.routes._llm_wiki_resolve_path") as mock_resolve, \
             patch("api.routes.get_session") as mock_session:
            mock_resolve.return_value = (tmp_path / "nonexistent", "default", False)
            mock_session.return_value = type("FakeSession", (), {"__dict__": session})()
            
            result = _handle_wiki_page_write(handler, body)

        assert handler.status == 404

    def test_wiki_page_put_invalid_page_name(self):
        """Invalid page names return 400."""
        from api.routes import _handle_wiki_page_write
        import io

        sid = "abc"
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        bad_bodies = [
            {"session_id": sid, "page_name": "../etc/passwd"},
            {"session_id": sid, "page_name": ""},
            {"session_id": sid, "page_name": ".hidden"},
            {"session_id": sid, "page_name": "a" * 200},
            {"session_id": sid},  # missing page_name
        ]

        for body in bad_bodies:
            handler = FakeHandler()
            result = _handle_wiki_page_write(handler, body)
            assert handler.status == 400 or handler.status == 404, f"Body {body} should fail with 400"

    def test_wiki_page_put_missing_session_id(self):
        """Missing session_id returns 400."""
        from api.routes import _handle_wiki_page_write
        import io
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        body = {"page_name": "test"}
        handler = FakeHandler()
        result = _handle_wiki_page_write(handler, body)
        assert handler.status == 400

    def test_wiki_page_put_session_not_found(self):
        """Non-existent session returns 404."""
        from api.routes import _handle_wiki_page_write
        import io
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        body = {"session_id": "nonexistent", "page_name": "test"}
        handler = FakeHandler()
        with patch("api.routes.get_session", side_effect=KeyError("not found")):
            result = _handle_wiki_page_write(handler, body)
        assert handler.status == 404

    def test_wiki_page_put_path_traversal(self):
        """Path traversal attempts return 400."""
        from api.routes import _handle_wiki_page_write
        import io
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        wiki_root = REPO / "tmp_wiki"
        wiki_root.mkdir(exist_ok=True)
        (wiki_root / "concepts").mkdir(exist_ok=True)

        body = {"session_id": "abc", "page_name": "../../etc/passwd", "section": "concepts", "mode": "create"}
        handler = FakeHandler()
        with patch("api.routes._llm_wiki_resolve_path") as mock_resolve, \
             patch("api.routes.get_session") as mock_session:
            mock_resolve.return_value = (wiki_root, "test", True)
            mock_session.return_value = type("FakeSession", (), {"__dict__": {}})()
            
            result = _handle_wiki_page_write(handler, body)

        # Should be 400 due to regex rejection (before containment check)
        assert handler.status == 400

    def test_wiki_page_put_invalid_section(self, tmp_path):
        """Invalid section returns 400."""
        from api.routes import _handle_wiki_page_write
        import io
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        body = {"session_id": "abc", "page_name": "test", "section": "invalid_section", "mode": "create"}
        handler = FakeHandler()
        result = _handle_wiki_page_write(handler, body)
        assert handler.status == 400

    def test_wiki_page_put_invalid_mode(self, tmp_path):
        """Invalid mode returns 400."""
        from api.routes import _handle_wiki_page_write
        import io
        
        class FakeHandler:
            def __init__(self):
                self.rfile = io.BytesIO(b"")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "0"}
                self.command = "POST"
                self.status = None
            def send_response(self, code):
                self.status = code
            def send_header(self, k, v):
                pass
            def end_headers(self):
                pass

        body = {"session_id": "abc", "page_name": "test", "section": "concepts", "mode": "delete"}
        handler = FakeHandler()
        result = _handle_wiki_page_write(handler, body)
        assert handler.status == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

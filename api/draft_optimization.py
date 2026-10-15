"""
Draft Optimization Module

Reduces latency for large-session composer drafts by replacing full-session
rewrites with atomic per-session draft files.

Flow:
  routes.py (POST /api/session/draft) -> DraftOptimization.save_draft()
  routes.py (GET /api/session/{id}) -> DraftOptimization.overlay_draft()
  models.py (Session.load) -> DraftOptimization.overlay_draft()
  streaming.py (_run_agent_streaming) -> DraftOptimization.discard_draft()
"""

import json
import os
import threading
from pathlib import Path

# Lock striping to serialize concurrent draft saves/deletes per session
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_MUTEX = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    """Get or create a per-session lock for serialized draft operations."""
    with _LOCKS_MUTEX:
        if session_id not in _LOCKS:
            _LOCKS[session_id] = threading.Lock()
        return _LOCKS[session_id]


class DraftOptimization:
    """Atomic per-session draft persistence with overlay loading."""

    DRAFTS_DIR = ".drafts"

    @staticmethod
    def _draft_path(session_store_dir: Path, session_id: str) -> Path:
        """Get the path to a session's dedicated draft file."""
        return session_store_dir / DraftOptimization.DRAFTS_DIR / f"{session_id}.json"

    @staticmethod
    def save_draft(session_store_dir: Path, session_id: str, draft: dict) -> None:
        """Save a draft atomically without rewriting the full session."""
        lock = _session_lock(session_id)
        with lock:
            draft_path = DraftOptimization._draft_path(session_store_dir, session_id)
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: write to temp file then rename
            tmp_path = draft_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(draft, f)
            os.replace(tmp_path, draft_path)

    @staticmethod
    def overlay_draft(session_store_dir: Path, session_id: str, session_data: dict) -> dict:
        """Overlay a dedicated draft onto loaded session data."""
        draft_path = DraftOptimization._draft_path(session_store_dir, session_id)
        if draft_path.exists():
            try:
                with open(draft_path) as f:
                    draft = json.load(f)
                session_data.update(draft)
            except (json.JSONDecodeError, OSError):
                pass  # Malformed draft fallback
        return session_data

    @staticmethod
    def discard_draft(session_store_dir: Path, session_id: str) -> None:
        """Remove a dedicated draft (e.g., after message sent)."""
        lock = _session_lock(session_id)
        with lock:
            draft_path = DraftOptimization._draft_path(session_store_dir, session_id)
            if draft_path.exists():
                try:
                    draft_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def cleanup_session_drafts(session_store_dir: Path, session_id: str) -> None:
        """Clean up drafts when a session is deleted. Must run AFTER main session removal."""
        DraftOptimization.discard_draft(session_store_dir, session_id)

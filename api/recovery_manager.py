"""
Session Recovery Module

Provides crash recovery for long-running agent sessions by creating
checkpoints before risky operations and auto-rolling back on failure.

Architecture:
  - routes.py: Calls RecoveryManager.checkpoint_then_run() before agent execution
  - models.py: Calls RecoveryManager.create_checkpoint() before state mutations
  - streaming.py: Calls RecoveryManager.handle_stream_error() on stream failures
  - Each session gets its own checkpoint sequence with bounded retention

Flow:
  POST /api/session/send
    -> RecoveryManager.checkpoint_then_run()
      -> create_checkpoint() writes .checkpoints/{session_id}_{seq}.json
      -> _run_agent_streaming() executes
      -> on failure: rollback_to_checkpoint() restores from snapshot
      -> on success: keep last N checkpoints, delete oldest
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional, Callable, Any

# Lock striping for concurrent recovery operations
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_MUTEX = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    """Get or create a per-session lock for serialized recovery operations."""
    with _LOCKS_MUTEX:
        if session_id not in _LOCKS:
            _LOCKS[session_id] = threading.Lock()
        return _LOCKS[session_id]


class RecoveryManager:
    """Crash recovery for agent sessions with checkpoint/rollback semantics."""

    CHECKPOINT_DIR = ".checkpoints"
    MAX_CHECKPOINTS = 5

    @staticmethod
    def _checkpoint_path(session_store_dir: Path, session_id: str, seq: int) -> Path:
        """Get path for a specific checkpoint snapshot."""
        return session_store_dir / RecoveryManager.CHECKPOINT_DIR / f"{session_id}_{seq}.json"

    @staticmethod
    def create_checkpoint(session_store_dir: Path, session_id: str, session_data: dict) -> Optional[str]:
        """
        Create a checkpoint snapshot of the current session state.
        Returns checkpoint ID or None on failure.
        """
        lock = _session_lock(session_id)
        with lock:
            checkpoint_dir = session_store_dir / RecoveryManager.CHECKPOINT_DIR
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

            existing = sorted(checkpoint_dir.glob(f"{session_id}_*.json"))
            seq = len(existing) + 1

            # Enforce max checkpoints — remove oldest
            if len(existing) >= RecoveryManager.MAX_CHECKPOINTS:
                for old in existing[:len(existing) - RecoveryManager.MAX_CHECKPOINTS + 1]:
                    try:
                        old.unlink()
                    except OSError:
                        pass

            checkpoint_id = f"{session_id}_cp{seq}"
            checkpoint_path = RecoveryManager._checkpoint_path(session_store_dir, session_id, seq)

            # Atomic write: temp then rename
            tmp_path = checkpoint_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump({
                    "checkpoint_id": checkpoint_id,
                    "timestamp": time.time(),
                    "session_data": session_data,
                }, f)
            os.replace(tmp_path, checkpoint_path)

            return checkpoint_id

    @staticmethod
    def rollback_to_checkpoint(session_store_dir: Path, session_id: str, checkpoint_id: str) -> Optional[dict]:
        """Restore session state from a checkpoint."""
        checkpoint_dir = session_store_dir / RecoveryManager.CHECKPOINT_DIR
        if not checkpoint_dir.exists():
            return None

        for checkpoint_file in checkpoint_dir.glob(f"{session_id}_*.json"):
            try:
                with open(checkpoint_file) as f:
                    checkpoint = json.load(f)
                if checkpoint.get("checkpoint_id") == checkpoint_id:
                    return checkpoint.get("session_data")
            except (json.JSONDecodeError, OSError):
                continue
        return None

    @staticmethod
    def checkpoint_then_run(
        session_store_dir: Path,
        session_id: str,
        session_data: dict,
        run_fn: Callable,
    ) -> Any:
        """
        Create a checkpoint, then run the function. If the function fails,
        roll back to the checkpoint.
        """
        checkpoint_id = RecoveryManager.create_checkpoint(session_store_dir, session_id, session_data)
        if not checkpoint_id:
            return run_fn()

        try:
            return run_fn()
        except Exception:
            if checkpoint_id:
                restored = RecoveryManager.rollback_to_checkpoint(session_store_dir, session_id, checkpoint_id)
                if restored:
                    session_data.clear()
                    session_data.update(restored)
            raise

    @staticmethod
    def handle_stream_error(session_store_dir: Path, session_id: str, error: Exception) -> bool:
        """
        Handle a streaming error by rolling back to the last checkpoint.
        Returns True if rollback succeeded.
        """
        checkpoint_dir = session_store_dir / RecoveryManager.CHECKPOINT_DIR
        if not checkpoint_dir.exists():
            return False

        checkpoints = sorted(checkpoint_dir.glob(f"{session_id}_*.json"))
        if not checkpoints:
            return False

        # Roll back to the latest checkpoint
        latest = checkpoints[-1]
        try:
            with open(latest) as f:
                checkpoint = json.load(f)
            restored = checkpoint.get("session_data")
            if restored:
                session_store_dir.mkdir(parents=True, exist_ok=True)
                session_file = session_store_dir / f"{session_id}.json"
                tmp_path = session_file.with_suffix(".tmp")
                with open(tmp_path, "w") as f:
                    json.dump(restored, f)
                os.replace(tmp_path, session_file)
                return True
        except (json.JSONDecodeError, OSError):
            pass
        return False

    @staticmethod
    def cleanup_session_checkpoints(session_store_dir: Path, session_id: str) -> None:
        """Remove all checkpoints when a session is deleted."""
        checkpoint_dir = session_store_dir / RecoveryManager.CHECKPOINT_DIR
        if not checkpoint_dir.exists():
            return

        lock = _session_lock(session_id)
        with lock:
            for checkpoint_file in checkpoint_dir.glob(f"{session_id}_*.json"):
                try:
                    checkpoint_file.unlink()
                except OSError:
                    pass

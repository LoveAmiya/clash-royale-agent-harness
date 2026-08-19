"""Shared write-lock and batch-policy primitives for rolling corpus storage."""

from __future__ import annotations

import os
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path


class CorpusError(ValueError):
    """Base error for deterministic corpus validation failures."""


class CorpusConflictError(CorpusError):
    """Raised when one battle ID is associated with conflicting facts."""


class CorpusWriterBusyError(CorpusError):
    """Raised when another corpus writer owns the lock."""


class CorpusWriterLock(AbstractContextManager):
    """Cross-process single-writer lock released automatically on process exit."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.handle.tell() == 0 and self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise CorpusWriterBusyError("another rolling corpus writer is already running") from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


@dataclass(frozen=True)
class BatchValidationPolicy:
    required_top_rank: int = 100
    ranked_player_target: int = 1000
    minimum_coverage: float = 0.99
    minimum_expansion_coverage: float = 0.99
    weekly_target_battles: int = 200_000


__all__ = [
    "BatchValidationPolicy",
    "CorpusConflictError",
    "CorpusError",
    "CorpusWriterBusyError",
    "CorpusWriterLock",
]

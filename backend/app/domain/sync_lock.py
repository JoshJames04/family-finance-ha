"""Nonblocking sync ownership, released by the OS even on process termination.

No SQLite write transaction spans provider I/O. Files contain no financial data;
never unlink a lock file, since existing owners may still hold its inode.
"""
from __future__ import annotations

import errno
import hashlib
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session


@contextmanager
def claim(session: Session, scope: str) -> Iterator[bool]:
    engine = session.get_bind().engine
    database = engine.url.database
    identity = (str(Path(database).resolve()) if database and database != ":memory:"
                else f"memory:{id(engine)}")
    key = hashlib.sha256(f"{identity}:{scope}".encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "family-finance-sync-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / key).open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            # Windows locks a byte range; ensure that byte exists.
            if handle.tell() == 0:
                handle.write(b"0")
            handle.flush()
            handle.seek(0)
        else:
            import fcntl
        try:
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            yield False
            return
        try:
            yield True
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

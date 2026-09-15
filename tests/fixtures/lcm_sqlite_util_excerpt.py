"""VERBATIM EXCERPT of hermes-lcm's sqlite_util.py, used as a test fixture.

Source: stephenschoettler/hermes-lcm @ 8d1b1e6d3d63f5fc7b209e8d7ec1dc9b814f2e54
        sqlite_util.py, lines 1-120 (imports + the artifact-permission helpers)

Trimmed to the call graph reached by _chmod_sqlite_artifact_at() so the test
exercises the real upstream function, not a mock. Do not edit by hand: if the
anchor in the real plugin moves, refresh this from the installed plugin file
(the boot patcher will fail closed in production either way).
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat


_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _sqlite_artifact_error(path: Path, reason: str) -> OSError:
    return OSError(errno.EPERM, f"refusing SQLite artifact {path.name!r}: {reason}", str(path))


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_sqlite_artifact(path: Path, file_stat: os.stat_result) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise _sqlite_artifact_error(path, "not a regular file")
    if file_stat.st_nlink != 1:
        raise _sqlite_artifact_error(path, "link count is not one")


def _require_sqlite_artifact_absent(path: Path, *, directory_fd: int) -> None:
    """Accept a vanished sidecar only while its directory entry stays absent."""
    try:
        current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    _validate_sqlite_artifact(path, current)
    raise _sqlite_artifact_error(path, "directory entry changed while opening")


def _open_private_sqlite_directory(path: Path) -> int:
    directory = path.parent
    expected = os.stat(directory, follow_symlinks=False)
    if not stat.S_ISDIR(expected.st_mode):
        raise _sqlite_artifact_error(path, "parent is not a regular directory")
    if expected.st_mode & 0o022:
        raise _sqlite_artifact_error(path, "parent directory is writable by another user")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    directory_fd = os.open(directory, flags)
    opened = os.fstat(directory_fd)
    if not stat.S_ISDIR(opened.st_mode) or not _same_file_identity(expected, opened):
        os.close(directory_fd)
        raise _sqlite_artifact_error(path, "parent directory changed while opening")
    return directory_fd


def _chmod_sqlite_artifact_at(
    path: Path,
    *,
    directory_fd: int,
    create: bool,
    allow_sidecar_disappearance: bool = False,
) -> bool:
    expected: os.stat_result | None
    try:
        expected = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return False
        expected = None
    if expected is not None:
        _validate_sqlite_artifact(path, expected)

    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    if expected is None:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError:
        if expected is not None:
            raise
        return _chmod_sqlite_artifact_at(
            path,
            directory_fd=directory_fd,
            create=False,
        )
    except FileNotFoundError:
        if not allow_sidecar_disappearance:
            raise
        _require_sqlite_artifact_absent(path, directory_fd=directory_fd)
        return False
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise _sqlite_artifact_error(path, "not a regular file")
        if expected is not None and not _same_file_identity(expected, opened):
            raise _sqlite_artifact_error(path, "directory entry changed while opening")
        if opened.st_nlink == 0 and allow_sidecar_disappearance:
            _require_sqlite_artifact_absent(path, directory_fd=directory_fd)
            return False
        _validate_sqlite_artifact(path, opened)
        os.fchmod(fd, 0o600)
        restricted = os.fstat(fd)
        if restricted.st_nlink == 0 and allow_sidecar_disappearance:
            _require_sqlite_artifact_absent(path, directory_fd=directory_fd)
            return False
        if restricted.st_nlink != 1:
            raise _sqlite_artifact_error(path, "link count changed while restricting permissions")
    finally:
        os.close(fd)
    return True

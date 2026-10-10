"""Filesystem helpers for untrusted sandbox content.

The sandbox writes into a directory the host also reads. Everything here treats
that directory as hostile: no symlink is ever followed, special files are
refused, and every path is opened relative to a directory file descriptor.
"""

from __future__ import annotations

import os
import shutil
import stat
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .errors import LimitExceeded, PathViolation

MAX_COMPONENT = 255
MAX_PATH = 4096
MAX_DEPTH = 64
_CHUNK = 1024 * 1024


def validate_relpath(rel: str) -> PurePosixPath:
    """Return a safe relative POSIX path or raise PathViolation."""
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PATH:
        raise PathViolation("path must be a non-empty string of bounded length")
    if rel.startswith("/") or "\\" in rel or any(ord(c) < 32 or ord(c) == 127 for c in rel):
        raise PathViolation(f"absolute, backslash or control characters in path: {rel!r}")
    parts = rel.split("/")
    if len(parts) > MAX_DEPTH:
        raise PathViolation("path is nested too deeply")
    for part in parts:
        if part in ("", ".", ".."):
            raise PathViolation(f"empty, '.' or '..' component in path: {rel!r}")
        if len(part) > MAX_COMPONENT:
            raise PathViolation("path component too long")
        folded = part.rstrip(" .").casefold()
        if folded in (".git", "git~1"):
            raise PathViolation(f"Git metadata path is not allowed: {rel!r}")
    return PurePosixPath(rel)


@dataclass(frozen=True)
class TreeEntry:
    rel: str
    kind: str  # "dir" | "file" | "symlink"
    size: int = 0
    executable: bool = False
    target: str = ""


@dataclass(frozen=True)
class TreeLimits:
    max_files: int = 20_000
    max_bytes: int = 200 * 1024 * 1024
    max_file_bytes: int = 50 * 1024 * 1024


def _is_within(path: str, root_real: str) -> bool:
    return path == root_real or path.startswith(root_real + os.sep)


def scan_tree(
    root: Path,
    *,
    exclude: Iterable[str] = (),
    skip_top: Iterable[str] = (),
    limits: TreeLimits = TreeLimits(),
) -> list[TreeEntry]:
    """Validate and list a tree without following symlinks.

    `exclude` are top-level names left out entirely (e.g. node_modules); they
    are not scanned. `skip_top` are trusted top-level names (e.g. a worktree's
    own `.git` file) skipped before validation. The tree must be quiescent:
    callers stop the sandbox first.
    """
    root = Path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise PathViolation("tree root must be a real directory, not a symlink")
    root_real = os.path.realpath(root)
    excluded = set(exclude)
    skipped = set(skip_top)
    entries: list[TreeEntry] = []
    files = 0
    total = 0

    def walk(dir_path: str, rel_prefix: str, depth: int) -> None:
        nonlocal files, total
        if depth > MAX_DEPTH:
            raise PathViolation("tree is nested too deeply")
        with os.scandir(dir_path) as it:
            children = []
            for child in it:
                if depth == 0 and (child.name in skipped or child.name in excluded):
                    continue
                if files + len(children) >= limits.max_files:
                    raise LimitExceeded("snapshot exceeds entry-count bound")
                children.append(child)
            children.sort(key=lambda e: e.name)
        for child in children:
            if depth == 0 and (child.name in skipped or child.name in excluded):
                continue
            rel = f"{rel_prefix}{child.name}"
            validate_relpath(rel)
            st = child.stat(follow_symlinks=False)
            files += 1
            if files > limits.max_files:
                raise LimitExceeded("snapshot exceeds entry-count bound")
            mode = st.st_mode
            if stat.S_ISDIR(mode):
                entries.append(TreeEntry(rel, "dir"))
                walk(child.path, rel + "/", depth + 1)
            elif stat.S_ISREG(mode):
                total += st.st_size
                if files > limits.max_files or total > limits.max_bytes:
                    raise LimitExceeded("snapshot exceeds file-count or byte bound")
                if st.st_size > limits.max_file_bytes:
                    raise LimitExceeded(f"file too large: {rel}")
                entries.append(TreeEntry(rel, "file", st.st_size, bool(mode & 0o111)))
            elif stat.S_ISLNK(mode):
                target = os.readlink(child.path)
                if not target or "\0" in target or os.path.isabs(target):
                    raise PathViolation(f"symlink with absolute or empty target: {rel}")
                if not _is_within(os.path.realpath(child.path), root_real):
                    raise PathViolation(f"symlink escapes the tree: {rel}")
                entries.append(TreeEntry(rel, "symlink", target=target))
            else:
                raise PathViolation(f"special file type is not allowed: {rel}")

    walk(str(root), "", 0)
    return entries


def _dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_parent(root: Path, parts: tuple[str, ...], *, create: bool, mode: int) -> int:
    """Open the parent directory of parts[-1] beneath root; never follows symlinks."""
    fd = os.open(root, _dir_flags())
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                    os.chmod(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, _dir_flags(), dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_beneath(root: Path, rel: str, flags: int, *, mode: int = 0o666, create_dirs: bool = False) -> int:
    parts = validate_relpath(rel).parts
    parent = _open_parent(Path(root), parts, create=create_dirs, mode=0o777)
    try:
        return os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, mode, dir_fd=parent)
    finally:
        os.close(parent)


def read_file_beneath(root: Path, rel: str, *, max_bytes: int = 5 * 1024 * 1024) -> bytes:
    fd = open_beneath(root, rel, os.O_RDONLY | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathViolation(f"not a regular file: {rel}")
        if st.st_size > max_bytes:
            raise LimitExceeded(f"file too large to read through the broker: {rel}")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise LimitExceeded("file grew beyond read limit")
            return data
    finally:
        os.close(fd)


def write_file_beneath(root: Path, rel: str, data: bytes, *, executable: bool = False) -> None:
    fd = open_beneath(root, rel, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK, create_dirs=True)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PathViolation(f"not a regular file: {rel}")
        os.ftruncate(fd, 0)
        os.fchmod(fd, 0o777 if executable else 0o666)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def atomic_write_file_beneath(root: Path, rel: str, data: bytes) -> None:
    """Publish one complete file via dir-fd rename; failed writes retain the old file."""
    parts = validate_relpath(rel).parts
    parent = _open_parent(root, parts, create=True, mode=0o777)
    temporary = '.source-edit-' + secrets.token_hex(16)
    fd = None
    try:
        try:
            target = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(target.st_mode):
                raise PathViolation(f'not a regular file: {rel}')
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError('source write made no progress')
            view = view[written:]
        os.fchmod(fd, 0o666)
        os.fsync(fd)
        os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        if fd is not None:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def remove_beneath(root: Path, rel: str) -> None:
    parts = validate_relpath(rel).parts
    parent = _open_parent(Path(root), parts, create=False, mode=0o777)
    try:
        st = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            raise PathViolation("refusing to remove a directory through the file tool")
        os.unlink(parts[-1], dir_fd=parent)
    finally:
        os.close(parent)


def clear_dir(dest: Path, *, keep: Iterable[str] = ()) -> None:
    kept = set(keep)
    with os.scandir(dest) as it:
        children = list(it)
    for child in children:
        if child.name in kept:
            continue
        if child.is_dir(follow_symlinks=False):
            shutil.rmtree(child.path)
        else:
            os.unlink(child.path)


def copy_entries(src_root: Path, entries: list[TreeEntry], dest_root: Path, *, sandbox_visible: bool) -> None:
    """Copy scanned entries without following links; dest must be empty/cleared.

    sandbox_visible makes files and directories writable by the unprivileged
    container user; otherwise modes are normalised to 0644/0755.
    """
    dir_mode = 0o777 if sandbox_visible else 0o755
    for entry in entries:
        parts = validate_relpath(entry.rel).parts
        parent = _open_parent(dest_root, parts, create=True, mode=dir_mode)
        try:
            name = parts[-1]
            if entry.kind == "dir":
                try:
                    os.mkdir(name, dir_mode, dir_fd=parent)
                except FileExistsError:
                    pass
                os.chmod(name, dir_mode, dir_fd=parent)
            elif entry.kind == "symlink":
                os.symlink(entry.target, name, dir_fd=parent)
            else:
                perm = (0o777 if entry.executable else 0o666) if sandbox_visible else (0o755 if entry.executable else 0o644)
                sfd = open_beneath(src_root, entry.rel, os.O_RDONLY)
                try:
                    if not stat.S_ISREG(os.fstat(sfd).st_mode):
                        raise PathViolation(f'file changed into a special file: {entry.rel}')
                    dfd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, perm, dir_fd=parent)
                    try:
                        os.fchmod(dfd, perm)
                        while chunk := os.read(sfd, _CHUNK):
                            view = memoryview(chunk)
                            while view:
                                view = view[os.write(dfd, view) :]
                    finally:
                        os.close(dfd)
                finally:
                    os.close(sfd)
        finally:
            os.close(parent)


def sha256_tree(root: Path, entries: list[TreeEntry]) -> str:
    """Deterministic digest of a scanned tree (paths, kinds, modes, contents)."""
    import hashlib

    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e.rel):
        digest.update(f"{entry.kind}\0{entry.rel}\0{int(entry.executable)}\0{entry.target}\0".encode())
        if entry.kind == "file":
            fd = open_beneath(root, entry.rel, os.O_RDONLY)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise PathViolation(f'file changed into a special file: {entry.rel}')
                inner = hashlib.sha256()
                while chunk := os.read(fd, _CHUNK):
                    inner.update(chunk)
            finally:
                os.close(fd)
            digest.update(inner.digest())
    return digest.hexdigest()

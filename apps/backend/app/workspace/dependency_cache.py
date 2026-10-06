"""Supervisor-owned tarball cache. Every hit is verified again before reuse."""
import hashlib
import fcntl
import hmac
import os
import stat
import time
from pathlib import Path
from .errors import SandboxError

MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHE_ENTRIES = 10000
MAX_ENTRY_BYTES = 50 * 1024 * 1024


class DependencyCache:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SandboxError('dependency cache must be a private directory owned by supervisor')
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(self.fd)
        if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
            os.close(self.fd)
            raise SandboxError('dependency cache directory changed')

    def close(self):
        os.close(self.fd)

    @staticmethod
    def name(expected):
        return expected.hex() + '.tgz'

    def get(self, expected, *, max_bytes, check):
        name = self.name(expected)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o022 or info.st_size > min(max_bytes, MAX_ENTRY_BYTES)):
                raise SandboxError('dependency cache entry ownership/type/size mismatch')
            data = bytearray()
            while True:
                check()
                chunk = f.read(min(65536, min(max_bytes, MAX_ENTRY_BYTES) - len(data) + 1))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > min(max_bytes, MAX_ENTRY_BYTES):
                    raise SandboxError('dependency cache entry exceeds byte bound')
        if not hmac.compare_digest(hashlib.sha512(data).digest(), expected):
            # A corrupt entry is never sent to target/npm. Download and verify anew.
            try:
                os.unlink(name, dir_fd=self.fd)
            except FileNotFoundError:
                pass
            return None
        try:
            os.utime(name, (time.time(), time.time()), dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            pass  # An eviction does not invalidate the verified bytes already read.
        return bytes(data)

    def put(self, expected, data):
        fd = os.open('.cache.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SandboxError('unsafe cache lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._put_locked(expected, data)
        finally:
            os.close(fd)

    def _put_locked(self, expected, data):
        if len(data) > MAX_ENTRY_BYTES:
            return
        if not hmac.compare_digest(hashlib.sha512(data).digest(), expected):
            raise SandboxError('cannot cache unverified dependency bytes')
        entries = []
        for name in os.listdir(self.fd):
            if name.startswith('.incoming-') and len(name) == 42 and all(c in '0123456789abcdef' for c in name[10:]):
                # All cache writers hold .cache.lock; leftovers are interrupted
                # writes, never an active writer or an approval-pinned artifact.
                info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise SandboxError('unsafe interrupted cache write')
                os.unlink(name, dir_fd=self.fd)
                continue
            if len(name) != 132 or not name.endswith('.tgz') or any(c not in '0123456789abcdef' for c in name[:-4]):
                continue
            try:
                info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise SandboxError('unsafe dependency cache entry')
            entries.append((info.st_mtime, name, info.st_size))
        size = sum(e[2] for e in entries)
        count_entries = len(entries)
        for _, name, count in sorted(entries):
            if size + len(data) <= MAX_CACHE_BYTES and count_entries < MAX_CACHE_ENTRIES:
                break
            try:
                os.unlink(name, dir_fd=self.fd)
            except FileNotFoundError:
                pass
            size -= count
            count_entries -= 1
        # O_EXCL + NOFOLLOW and rename relative to the checked directory fd.
        import secrets
        temporary = '.incoming-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.name(expected), src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

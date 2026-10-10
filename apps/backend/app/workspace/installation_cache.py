"""Private, bounded snapshots published only after the fixed offline installer.

Targets never mount this cache. Every restore verifies tree contents, and the
caller publishes before any project build/test/start process can change them.
"""
import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from . import fsutil
from .errors import SandboxError, WorkspaceError
from .runspec import atomic_write_json

MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 16
VERSION = 1


class InstallationCache:
    def __init__(self, root, limits):
        self.root, self.limits = Path(root), limits
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._trusted(self.root)
        from .bounded_io import DEPENDENCY_LIMITS
        self.tree_limits = DEPENDENCY_LIMITS

    @staticmethod
    def _trusted(path):
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise SandboxError('installation cache must be a private supervisor directory')

    @contextmanager
    def locked(self):
        fd = os.open(self.root / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SandboxError('unsafe installation cache lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def key(source, image, env, limits):
        inputs = {name: hashlib.sha256(fsutil.read_file_beneath(source, name,
                  max_bytes=limits.max_snapshot_bytes)).hexdigest()
                  for name in ('package.json', 'package-lock.json')}
        return hashlib.sha256(json.dumps({'version': VERSION, 'inputs': inputs, 'image': image,
            'env': env, 'installer': 'npm-ci-offline-ignore-scripts',
            'max_bytes': limits.max_snapshot_bytes, 'max_files': limits.max_snapshot_files,
            'installer_code': {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                               for name in ('installation_cache.py', 'sandbox.py', 'bounded_io.py', 'dependencies.py')}},
            sort_keys=True).encode()).hexdigest()

    def restore(self, key, source, check):
        with self.locked():
            entry = self.root / key
            if not entry.exists():
                return None
            self._trusted(entry)
            try:
                record = json.loads(fsutil.read_file_beneath(entry, 'receipt.json', max_bytes=16384))
                tree = entry / 'node_modules'
                entries = fsutil.scan_tree(tree, limits=self.tree_limits)
                if record.get('key') != key or fsutil.sha256_tree(tree, entries) != record.get('tree_digest'):
                    raise SandboxError('installation cache integrity mismatch')
            except (OSError, ValueError, TypeError, AttributeError, WorkspaceError):
                shutil.rmtree(entry)
                return None  # Corruption is a miss; run the installer again.
            check()
            with tempfile.TemporaryDirectory(dir=source.parent, prefix='.install-restore-') as temporary:
                stage = Path(temporary) / 'node_modules'
                stage.mkdir(mode=0o755)
                stage.chmod(0o755)
                fsutil.copy_entries(tree, entries, stage, sandbox_visible=True)
                copied = fsutil.scan_tree(stage, limits=self.tree_limits)
                if fsutil.sha256_tree(stage, copied) != record['tree_digest']:
                    raise SandboxError('restored installation digest mismatch')
                check()
                destination, previous = source / 'node_modules', Path(temporary) / 'previous'
                had_previous = destination.exists() or destination.is_symlink()
                if had_previous:
                    os.replace(destination, previous)
                try:
                    os.replace(stage, destination)
                except BaseException:
                    if had_previous:
                        os.replace(previous, destination)
                    raise
            os.utime(entry, None)
            return record

    def publish(self, key, source, origin, check):
        # scan_tree rejects links that escape node_modules, special files and .git.
        tree = source / 'node_modules'
        if not tree.exists():
            return  # A dependency-free install has no useful tree to cache.
        entries = fsutil.scan_tree(tree, limits=self.tree_limits)
        size = sum(e.size for e in entries)
        if size > MAX_BYTES:
            return
        tree_digest = fsutil.sha256_tree(tree, entries)
        check()
        with self.locked():
            rows = []
            for entry in self.root.iterdir():
                if entry.name.startswith('.incoming-'):
                    self._trusted(entry)
                    shutil.rmtree(entry)
                elif len(entry.name) == 64 and all(c in '0123456789abcdef' for c in entry.name):
                    self._trusted(entry)
                    try:
                        old = fsutil.scan_tree(entry / 'node_modules', limits=self.tree_limits)
                        receipt = fsutil.read_file_beneath(entry, 'receipt.json', max_bytes=16384)
                    except (OSError, WorkspaceError):
                        shutil.rmtree(entry)
                        continue
                    rows.append((entry.stat().st_mtime, entry, sum(e.size for e in old) + len(receipt)))
            if any(row[1].name == key for row in rows):
                return
            total = sum(row[2] for row in rows)
            while rows and (total + size + 16384 > MAX_BYTES or len(rows) >= MAX_ENTRIES):
                _, entry, count = min(rows)
                shutil.rmtree(entry)
                total -= count
                rows = [row for row in rows if row[1] != entry]
            with tempfile.TemporaryDirectory(dir=self.root, prefix='.incoming-') as temporary:
                stage = Path(temporary)
                copied_tree = stage / 'node_modules'
                copied_tree.mkdir()
                fsutil.copy_entries(tree, entries, copied_tree, sandbox_visible=False)
                if fsutil.sha256_tree(copied_tree, fsutil.scan_tree(copied_tree, limits=self.tree_limits)) != tree_digest:
                    raise SandboxError('installation changed during snapshot publication')
                record = {'key': key, 'tree_digest': tree_digest, 'bytes': size,
                          'origin': origin, 'created_at': time.time()}
                atomic_write_json(stage / 'receipt.json', record)
                check()
                os.replace(stage, self.root / key)

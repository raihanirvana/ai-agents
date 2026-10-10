"""Installation snapshot reuse: hash once, hardlink restores, detect in-place tampering.

Local files only; no Docker, registry or provider. Not dependency-compatibility evidence.
"""
import os

import pytest

from app.workspace import fsutil
from app.workspace.installation_cache import InstallationCache

KEY = 'a' * 64


def project(path, content='module.exports = 1'):
    package = path / 'node_modules' / 'pkg'
    package.mkdir(parents=True)
    (package / 'index.js').write_text(content)
    (path / 'node_modules' / '.bin').mkdir()
    os.symlink('../pkg/index.js', path / 'node_modules' / '.bin' / 'pkg')
    return path


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(InstallationCache, '_verified', {})
    root = tmp_path / 'cache'
    root.mkdir(mode=0o700)
    return InstallationCache(root, None)


def count_hashes(monkeypatch):
    calls = []
    original = fsutil.sha256_tree
    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(fsutil, 'sha256_tree', counted)
    return calls


def test_restore_hardlinks_verified_entry_without_rehashing(tmp_path, cache, monkeypatch):
    origin = project(tmp_path / 'origin')
    cache.publish(KEY, origin, {'argv': ['npm', 'ci']}, lambda: None)
    hashes = count_hashes(monkeypatch)
    for name in ('one', 'two'):
        workspace = tmp_path / name
        workspace.mkdir()
        record = cache.restore(KEY, workspace, lambda: None)
        assert record['restore'] == 'hardlink'
        restored = workspace / 'node_modules' / 'pkg' / 'index.js'
        assert restored.read_text() == 'module.exports = 1'
        assert restored.stat().st_ino == (cache.root / KEY / 'node_modules' / 'pkg' / 'index.js').stat().st_ino
        assert os.readlink(workspace / 'node_modules' / '.bin' / 'pkg') == '../pkg/index.js'
    assert hashes == []  # Published contents were hashed once at publication.


def test_new_process_hashes_once_then_reuses(tmp_path, cache, monkeypatch):
    cache.publish(KEY, project(tmp_path / 'origin'), {}, lambda: None)
    monkeypatch.setattr(InstallationCache, '_verified', {})  # Simulates a fresh supervisor process.
    hashes = count_hashes(monkeypatch)
    for name in ('one', 'two', 'three'):
        (tmp_path / name).mkdir()
        assert cache.restore(KEY, tmp_path / name, lambda: None)
    assert len(hashes) == 1


def test_in_place_change_through_any_link_forces_rehash_and_evicts(tmp_path, cache):
    cache.publish(KEY, project(tmp_path / 'origin'), {}, lambda: None)
    workspace = tmp_path / 'one'
    workspace.mkdir()
    cache.restore(KEY, workspace, lambda: None)
    # Same inode: tampering is visible to the cache through metadata, then content.
    (workspace / 'node_modules' / 'pkg' / 'index.js').write_text('module.exports = 2')
    (tmp_path / 'two').mkdir()
    assert cache.restore(KEY, tmp_path / 'two', lambda: None) is None
    assert not (cache.root / KEY).exists()


def test_unlinkable_filesystem_falls_back_to_verified_copy(tmp_path, cache, monkeypatch):
    cache.publish(KEY, project(tmp_path / 'origin'), {}, lambda: None)
    def cross_device(*args, **kwargs):
        raise OSError(18, 'Invalid cross-device link')
    monkeypatch.setattr(fsutil, 'link_entries', cross_device)
    workspace = tmp_path / 'one'
    workspace.mkdir()
    (workspace / 'node_modules').mkdir()
    (workspace / 'node_modules' / 'stale.js').write_text('old')
    record = cache.restore(KEY, workspace, lambda: None)
    assert record['restore'] == 'copy'
    restored = workspace / 'node_modules' / 'pkg' / 'index.js'
    assert restored.read_text() == 'module.exports = 1' and not (workspace / 'node_modules' / 'stale.js').exists()
    assert restored.stat().st_ino != (cache.root / KEY / 'node_modules' / 'pkg' / 'index.js').stat().st_ino

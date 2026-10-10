"""Trusted archive fixtures; no target code or Docker executed in these tests."""
import io
import os
import tarfile
import time

import pytest

from app.workspace import bounded_io, fsutil
from app.workspace.errors import PathViolation, LimitExceeded
from app.workspace.runspec import ResourceLimits


def fixture_archive(monkeypatch, rows):
    def stream(argv, *, output=None, **kwargs):
        if output is None:
            return 0, b'', b'', False, False, False
        with tarfile.open(fileobj=output, mode='w') as tar:
            for path, kind, value in rows:
                member = tarfile.TarInfo(path)
                if kind == 'link':
                    member.type, member.linkname = tarfile.SYMTYPE, value
                    tar.addfile(member)
                elif kind == 'dir':
                    member.type = tarfile.DIRTYPE
                    tar.addfile(member)
                else:
                    data = value.encode()
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
        return 0, b'', b'', False, False, False
    monkeypatch.setattr(bounded_io, 'stream_command', stream)


def source_tree(tmp_path):
    source = tmp_path / 'src'
    dependency = source / 'node_modules' / 'pkg'
    dependency.mkdir(parents=True)
    (dependency / 'index.js').write_text('immutable installed package')
    (source / 'app.js').write_text('old')
    return source, dependency / 'index.js'


def run(source, **kwargs):
    bounded_io.import_work('unused', 'fixture', source, ResourceLimits(max_snapshot_files=2),
                           deadline=time.monotonic() + 10, cancelled=lambda: False, **kwargs)


def test_readonly_export_preserves_dependency_inode_without_copy(tmp_path, monkeypatch):
    source, package = source_tree(tmp_path)
    inode = package.stat().st_ino
    fixture_archive(monkeypatch, [('app.js', 'file', 'new'),
                                  ('node_modules', 'link', '/installed/node_modules')])
    run(source, preserve_dependencies=True)
    assert (source / 'app.js').read_text() == 'new'
    assert package.stat().st_ino == inode
    assert package.read_text() == 'immutable installed package'


@pytest.mark.parametrize('rows', [
    [('node_modules', 'link', '/etc')],
    [('node_modules', 'dir', ''), ('node_modules/pkg.js', 'file', 'bad')],
    [('node_modules', 'link', '/installed/node_modules')] * 2,
    [('../escape', 'file', 'bad')],
    [('.git/config', 'file', 'bad')],
])
def test_host_source_survives_rejected_archive(tmp_path, monkeypatch, rows):
    source, package = source_tree(tmp_path)
    fixture_archive(monkeypatch, rows)
    with pytest.raises((PathViolation, LimitExceeded)):
        run(source, preserve_dependencies=True)
    assert (source / 'app.js').read_text() == 'old'
    assert package.read_text() == 'immutable installed package'


@pytest.mark.parametrize('target', ['/nonexistent-absolute-target', '../missing'])
def test_install_rejects_dangling_dependency_root_symlink(tmp_path, monkeypatch, target):
    source, package = source_tree(tmp_path)
    fixture_archive(monkeypatch, [('app.js', 'file', 'new'), ('node_modules', 'link', target)])
    with pytest.raises(PathViolation):
        run(source, install_phase=True)
    assert package.exists() and (source / 'app.js').read_text() == 'old'


def test_install_counts_dependencies_separately(tmp_path, monkeypatch):
    source, _ = source_tree(tmp_path)
    rows = [('app.js', 'file', 'new'), ('node_modules', 'dir', '')]
    rows += [(f'node_modules/pkg-{i}.js', 'file', 'package') for i in range(8)]
    fixture_archive(monkeypatch, rows)
    run(source, install_phase=True)
    assert (source / 'node_modules/pkg-7.js').exists()
    assert all((source / 'node_modules' / name).is_dir() for name in bounded_io.DEPENDENCY_SCRATCH)


def test_install_still_enforces_dependency_limit(tmp_path, monkeypatch):
    source, _ = source_tree(tmp_path)
    monkeypatch.setattr(bounded_io, 'DEPENDENCY_LIMITS', fsutil.TreeLimits(2, 1024))
    fixture_archive(monkeypatch, [('node_modules', 'dir', '')] +
                    [(f'node_modules/pkg-{i}.js', 'file', 'x') for i in range(3)])
    with pytest.raises(LimitExceeded):
        run(source, install_phase=True)
    assert (source / 'app.js').read_text() == 'old'


def test_publish_failure_rolls_back_source_and_dependency(tmp_path, monkeypatch):
    source, package = source_tree(tmp_path)
    inode = package.stat().st_ino
    fixture_archive(monkeypatch, [('app.js', 'file', 'new'), ('node_modules', 'link', '/installed/node_modules')])
    replace = os.replace
    def fail_publication(src, dest):
        if str(src).endswith('/tree'):
            raise OSError('publication failure fixture')
        return replace(src, dest)
    monkeypatch.setattr(os, 'replace', fail_publication)
    with pytest.raises(OSError):
        run(source, preserve_dependencies=True)
    assert (source / 'app.js').read_text() == 'old'
    assert package.stat().st_ino == inode


def test_scratch_rejects_symlink(tmp_path):
    (tmp_path / '.vite').symlink_to(tmp_path / 'absent')
    with pytest.raises((PathViolation, FileExistsError)):
        bounded_io.prepare_dependency_scratch(tmp_path)


def test_cancelled_export_never_replaces_host_source(tmp_path, monkeypatch):
    source, package = source_tree(tmp_path)
    def stopped_stream(*args, **kwargs):
        return -9, b'', b'cancelled fixture', False, True, False
    monkeypatch.setattr(bounded_io, 'stream_command', stopped_stream)
    from app.workspace.errors import SandboxError
    with pytest.raises(SandboxError, match='interrupted'):
        run(source, preserve_dependencies=True)
    assert (source / 'app.js').read_text() == 'old' and package.exists()


def test_dependency_scratch_entries_count_toward_install_cap(tmp_path, monkeypatch):
    source, _ = source_tree(tmp_path)
    monkeypatch.setattr(bounded_io, 'DEPENDENCY_LIMITS', fsutil.TreeLimits(2, 1024))
    fixture_archive(monkeypatch, [('node_modules', 'dir', ''), ('node_modules/pkg.js', 'file', 'x')])
    with pytest.raises(LimitExceeded):
        run(source, install_phase=True)
    assert (source / 'app.js').read_text() == 'old'

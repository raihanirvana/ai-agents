"""Actual containers on isolated temporary source; no live project/worker/provider."""
import uuid

import pytest
from app.workspace import DockerSandbox, ResourceLimits
from app.workspace.bounded_io import DEPENDENCY_SCRATCH
from .conftest import IMAGE, FIXTURE, start

pytestmark = pytest.mark.docker


def test_readonly_package_mount_scratch_and_dependency_inode(docker_ready, tmp_path):
    source = tmp_path / 'source'
    package = source / 'node_modules' / 'fixture-package'
    package.mkdir(parents=True)
    source.chmod(0o755)
    package.chmod(0o755)
    (package / 'index.js').write_text("module.exports = require('fixture-peer')")
    peer = source / 'node_modules' / 'fixture-peer'
    peer.mkdir()
    (peer / 'index.js').write_text('module.exports = 42')
    inode = (package / 'index.js').stat().st_ino
    script = """
const fs=require('node:fs');
if(require('fixture-package')!==42) process.exit(1);
try { fs.writeFileSync('node_modules/fixture-package/index.js','bad'); process.exit(2); }
catch(e) { if(!['EROFS','EACCES','EPERM'].includes(e.code)) throw e; }
fs.writeFileSync('node_modules/.vite-temp/probe','temporary');
fs.mkdirSync('dist'); fs.writeFileSync('dist/index.html','built');
console.log('readonly and scratch OK');
"""
    sandbox = DockerSandbox(supervisor_id='readonly-review')
    name = 'aiagent-test-readonly-' + uuid.uuid4().hex[:10]
    result = sandbox.run(name=name, source=source, image=IMAGE, argv=['node', '-e', script],
        network='none', limits=ResourceLimits(), env={}, labels={}, timeout_s=30)
    assert result.exit_code == 0, result.stderr
    assert b'readonly and scratch OK' in result.stdout
    assert (package / 'index.js').read_text() == "module.exports = require('fixture-peer')"
    assert (package / 'index.js').stat().st_ino == inode
    assert (source / 'dist/index.html').read_text() == 'built'
    assert not (source / 'node_modules/.vite-temp/probe').exists()


def test_real_vite_install_checks_and_readonly_build(real_sup, manifest, monkeypatch):
    run = start(real_sup, manifest, egress=True)
    for path in sorted(FIXTURE.rglob('*')):
        if path.is_file():
            real_sup.write_file(run.ref, run.credential, str(path.relative_to(FIXTURE)), path.read_bytes())
    installed = real_sup.run_phase(run.ref, run.credential, 'install')
    assert installed.exit_code == 0, installed.stderr
    source = real_sup.src_dir(run.ref)
    probe = source / 'node_modules' / 'vite' / 'package.json'
    inode, content = probe.stat().st_ino, probe.read_bytes()
    built = real_sup.run_phase(run.ref, run.credential, 'build')
    assert built.exit_code == 0, built.stderr
    assert (source / 'dist/index.html').is_file()
    assert probe.stat().st_ino == inode and probe.read_bytes() == content
    assert all((source / 'node_modules' / name).is_dir() for name in DEPENDENCY_SCRATCH)
    tested = real_sup.run_phase(run.ref, run.credential, 'test')
    assert tested.exit_code == 0, tested.stderr
    import app.workspace.dependencies as dependencies
    def forbid_download(*args, **kwargs):
        pytest.fail('exact installation snapshot should avoid downloading or reinstalling')
    monkeypatch.setattr(dependencies, 'fetch_tarballs', forbid_download)
    reused = real_sup.run_phase(run.ref, run.credential, 'install')
    assert reused.exit_code == 0 and reused.cache_key, reused.stderr
    cached_inode = probe.stat().st_ino
    rebuilt = real_sup.run_phase(run.ref, run.credential, 'build')
    assert rebuilt.exit_code == 0, rebuilt.stderr
    assert probe.stat().st_ino == cached_inode and probe.read_bytes() == content


def test_export_quiesces_a_detached_writer(docker_ready, tmp_path):
    source = tmp_path / 'source'
    source.mkdir(mode=0o755)
    sandbox = DockerSandbox(supervisor_id='readonly-review')
    script = """
const fs=require('node:fs'), cp=require('node:child_process');
const writer=cp.spawn(process.execPath,['-e',
  "const fs=require('node:fs');setInterval(()=>fs.writeFileSync('/work/background.txt','background'),1)"],
  {detached:true,stdio:'ignore'}); writer.unref();
fs.writeFileSync('foreground.txt','foreground');
"""
    result = sandbox.run(name='aiagent-test-writer-' + uuid.uuid4().hex[:10], source=source,
        image=IMAGE, argv=['node', '-e', script], network='none', limits=ResourceLimits(),
        env={}, labels={}, timeout_s=30)
    assert result.exit_code == 0, result.stderr
    assert (source / 'foreground.txt').read_text() == 'foreground'
    assert not sandbox._state(result.container)


def test_dependencies_above_source_file_cap_are_not_seeded_or_exported(docker_ready, tmp_path):
    source = tmp_path / 'source'
    package = source / 'node_modules' / 'bulk-fixture'
    package.mkdir(parents=True)
    source.chmod(0o755)
    for index in range(20_005):
        (package / str(index)).touch()
    sentinel = package / '0'
    inode = sentinel.stat().st_ino
    sandbox = DockerSandbox(supervisor_id='readonly-review')
    script = "const fs=require('node:fs');if(fs.readdirSync('node_modules/bulk-fixture').length!==20005)process.exit(1);fs.writeFileSync('app.js','built');"
    result = sandbox.run(name='aiagent-test-bulk-' + uuid.uuid4().hex[:10], source=source,
        image=IMAGE, argv=['node', '-e', script], network='none',
        limits=ResourceLimits(max_snapshot_files=2), env={}, labels={}, timeout_s=60)
    assert result.exit_code == 0, result.stderr
    assert (source / 'app.js').read_text() == 'built'
    assert sentinel.stat().st_ino == inode

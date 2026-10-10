"""Seed inputs are bounded and immutable while publication replaces the source path."""
import shutil
import uuid
import pytest
from app.workspace import DockerSandbox, ResourceLimits
from app.workspace.command_seed import prepare
from app.workspace.errors import PathViolation, LimitExceeded
from .conftest import docker_ready, IMAGE  # noqa: F401


def test_unique_seed_excludes_dependencies_and_survives_source_replacement(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'test.cjs').write_text('original')
    (source / 'node_modules').mkdir()
    (source / 'node_modules/special').symlink_to('/etc/passwd')
    root, tree, inventory = prepare(source, ResourceLimits())
    try:
        old = tmp_path / 'old'; source.rename(old)
        source.mkdir(); (source / 'test.cjs').write_text('replacement')
        shutil.rmtree(old)
        assert (tree / 'test.cjs').read_text() == 'original'
        assert not (tree / 'node_modules').exists()
        assert 'test.cjs' in inventory.read_text()
    finally:
        shutil.rmtree(root)


@pytest.mark.parametrize('special', ['symlink', 'size'])
def test_seed_refuses_escape_or_oversized_source(tmp_path, special):
    source = tmp_path / 'source'; source.mkdir()
    if special == 'symlink':
        (source / 'escape').symlink_to('/etc/passwd')
        expected = PathViolation
    else:
        (source / 'large').write_bytes(b'x' * 1025); expected = LimitExceeded
    with pytest.raises(expected):
        prepare(source, ResourceLimits(max_snapshot_bytes=1024))
    assert not list(tmp_path.glob('.command-seed-*'))


@pytest.mark.docker
def test_repeated_commands_retain_test_cjs_after_atomic_import(docker_ready, tmp_path):
    source = tmp_path / 'source'; source.mkdir(mode=0o755)
    (source / 'test.cjs').write_text("const {test}=require('node:test');test('real test',()=>{});")
    sandbox = DockerSandbox(supervisor_id='seed-repeat')
    digests = set()
    for _ in range(6):
        result = sandbox.run(name='aiagent-seed-repeat-' + uuid.uuid4().hex[:10], image=IMAGE,
            source=source, argv=['node', '--test', 'test.cjs'], network='none', limits=ResourceLimits(),
            env={}, labels={}, timeout_s=45)
        assert result.exit_code == 0, result.stderr
        assert b'real test' in result.stdout
        assert (source / 'test.cjs').is_file()
        digests.add(result.seed_inventory_digest)
    assert len(digests) == 1 and None not in digests
    assert not list(tmp_path.glob('.command-seed-*'))

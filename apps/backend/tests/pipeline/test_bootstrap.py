"""Empty-base bootstrap contract, with real Git/Docker and labelled fake model fixtures."""
import json

import pytest

from app.pipeline.bootstrap import bootstrap_contract, generate_lock
from app.persistence.models import Project
from app.workers.queue import StaleLease
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.pipeline.test_product_loop import setup
from app.workspace.manifest import reference_manifest_dict


def package():
    return {'name': 'new-shopping-list', 'version': '1.0.0', 'private': True, 'type': 'module',
            'scripts': {'build': 'vite build', 'test': 'node --test'},
            'dependencies': {'react': '18.3.1', 'react-dom': '18.3.1'},
            'devDependencies': {'vite': '6.4.3', '@vitejs/plugin-react': '4.3.4'}}


def test_generated_lock_matches_declared_root_and_preserves_integrities():
    pkg = package()
    lock = generate_lock(pkg)
    assert lock['packages']['']['dependencies'] == pkg['dependencies']
    assert lock['packages']['']['devDependencies'] == pkg['devDependencies']
    assert lock['name'] == pkg['name']
    assert len(bootstrap_contract()['catalog_digest']) == 64
    assert all('integrity' in entry for name, entry in lock['packages'].items() if name)


@pytest.mark.parametrize('deps', [
    {'vite': '^6.4.3'}, {'vite': 'file:../host'}, {'vite': 'https://example.com/malicious.tgz'},
    {'unknown-package': '1.0.0'},
])
def test_unqualified_dependencies_are_rejected(deps):
    with pytest.raises(ValueError, match='unsupported bootstrap dependency'):
        generate_lock({**package(), 'devDependencies': deps})


@pytest.mark.parametrize('key', ['workspaces', 'overrides', 'optionalDependencies', 'peerDependencies', 'bundledDependencies'])
def test_resolution_overrides_are_rejected(key):
    with pytest.raises(ValueError, match='does not support'):
        generate_lock({**package(), key: {}})


def development_context(env, runtime):
    env.queue.lease_s = 600  # Container download/build may exceed the fixture's short 30s lease.
    ticket = env.approved_ticket()
    job = env.job('developer', 'implement', ticket=ticket, stage='development', lane='execution', runtime=runtime.name)
    return env.ctx(job)


def test_new_project_bootstrap_install_build_test_and_commit_from_empty_base(agent_env, tmp_path):
    env = agent_env
    runtime, _, _ = setup(env, tmp_path)
    raw = reference_manifest_dict()
    with env.db.write() as s:
        p = s.get(Project, env.project.id)
        p.workflow = {**p.workflow, 'pipeline': {'manifest': raw}}
    ctx = development_context(env, runtime)
    sup, started, manifest = runtime.workspace.start(ctx)
    broker = sup.broker(env.project.id)
    base = broker.accepted_sha()
    try:
        assert sup.list_files(started.ref, started.credential) == []
        sup.write_file(started.ref, started.credential, 'package.json', json.dumps(package()).encode())
        sup.write_file(started.ref, started.credential, 'index.html',
                       b'<div id="root"></div><script type="module" src="/src/main.jsx"></script>')
        sup.write_file(started.ref, started.credential, 'src/main.jsx',
                       b"import React from 'react';import{createRoot}from'react-dom/client';"
                       b"createRoot(document.getElementById('root')).render(React.createElement('h1',null,'Shopping list'));")
        sup.write_file(started.ref, started.credential, 'test/source.test.cjs',
                       b"const test=require('node:test');const assert=require('node:assert/strict');"
                       b"test('title exists',()=>assert.match(require('fs').readFileSync('src/main.jsx','utf8'),/Shopping list/));")
        result = runtime.workspace.bootstrap(ctx, sup, started)
        assert result['generated'] == 'package-lock.json'
        assert broker.accepted_sha() == base
        for phase in ('install', 'test', 'build'):
            command = sup.run_phase(started.ref, started.credential, phase)
            assert command.exit_code == 0, (phase, command.stderr, command.stdout)
        candidate = sup.submit_candidate(started.ref, started.credential, 'Scoped foundation with generated lock')
        assert broker.file_at(candidate['sha'], 'package-lock.json')
        assert broker.accepted_sha() == base  # No acceptance is bypassed by bootstrap.
        target = sup.build_target(started.ref, candidate['sha'], run_tests=True)
        assert target['dependency_digest'] and target['candidate_sha'] == candidate['sha']
    finally:
        ctx.stop_resources()


def test_existing_project_bootstrap_is_rejected_without_changing_lock(agent_env, tmp_path):
    env = agent_env
    runtime, _, _ = setup(env, tmp_path)
    ctx = development_context(env, runtime)
    sup, started, _ = runtime.workspace.start(ctx)
    try:
        with env.db.write() as s:
            project = s.get(Project, env.project.id)
            project.mode = 'existing'
            project.repo_ref = str(tmp_path / 'original-repository')
        sup.write_file(started.ref, started.credential, 'package-lock.json', b'original lock')
        with pytest.raises(ValueError, match='NEW empty accepted base'):
            runtime.workspace.bootstrap(ctx, sup, started)
        assert sup.read_file(started.ref, started.credential, 'package-lock.json') == b'original lock'
    finally:
        ctx.stop_resources()


def test_revoked_developer_cannot_bootstrap(agent_env, tmp_path):
    env = agent_env
    runtime, _, _ = setup(env, tmp_path)
    ctx = development_context(env, runtime)
    sup, started, _ = runtime.workspace.start(ctx)
    env.queue.cancel(ctx.lease.job_id, reason='scope revoked', actor='user')
    try:
        with pytest.raises(StaleLease):
            runtime.workspace.bootstrap(ctx, sup, started)
        assert not (sup.src_dir(started.ref) / 'package-lock.json').exists()
    finally:
        ctx.stop_resources()


def test_new_project_with_accepted_code_cannot_replace_its_lock(agent_env, tmp_path):
    from tests.pipeline.test_product_regressions import seed_baseline
    env = agent_env
    runtime, _, _ = setup(env, tmp_path)
    seed_baseline(env, runtime, {'package.json': json.dumps(package()), 'package-lock.json': 'accepted lock'})
    ctx = development_context(env, runtime)
    sup, started, _ = runtime.workspace.start(ctx)
    try:
        with pytest.raises(ValueError, match='NEW empty accepted base'):
            runtime.workspace.bootstrap(ctx, sup, started)
        assert sup.read_file(started.ref, started.credential, 'package-lock.json') == b'accepted lock'
    finally:
        ctx.stop_resources()


def test_qa_cannot_generate_source_lockfiles(agent_env, tmp_path):
    env = agent_env
    runtime, _, _ = setup(env, tmp_path)
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job('qa', 'qa_plan', ticket=ticket, stage='qa_plan', lane='execution', runtime=runtime.name))
    sup, started, _ = runtime.workspace.start(ctx)
    try:
        with pytest.raises(ValueError, match='only available to a developer'):
            runtime.workspace.bootstrap(ctx, sup, started)
        assert sup.list_files(started.ref, started.credential) == []
    finally:
        ctx.stop_resources()

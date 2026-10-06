"""Product lease adapter around the DEV-005 broker. Local run state adds restrictions, never authority."""
import base64
import json
import uuid
from pathlib import Path
from sqlalchemy import func, select
from app.domain import Actor, Attempt
from app.persistence.models import Project, Ticket, TicketVersion, Job, Candidate, Message
from app.agents.effects import append_effect
from app.persistence.transactions import bind_service
from app.workspace import WorkspaceSupervisor
from app.workspace.runspec import RunRef
from app.workspace.manifest import parse_manifest
from app.workspace import fsutil
from .contracts import QaPlan, digest_of
from .gates import node_gate


def cleanup_workspace(sup, ref, job_id, generation):
    directory = sup.root / ref.project_id / 'runs' / ref.run_id
    if not directory.exists():
        return
    if not (directory / 'runspec.json').exists():
        directory.rmdir()  # only an empty pre-creation intent; unexpected contents fail closed
        return
    local = sup._store(ref)
    spec = local.spec()
    if spec.provenance.get('job_id') != job_id or spec.generation != generation:
        raise ValueError('workspace cleanup ownership mismatch')
    try:
        local.state()
    except FileNotFoundError:
        if spec.attempt_ref in sup.broker(ref.project_id).refs() or sup.sandbox.owned(project_id=ref.project_id, run_id=ref.run_id):
            raise ValueError('partial workspace has unverified live resources')
        archive = sup.root / ref.project_id / 'archive' / ('partial-' + ref.run_id)
        archive.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(archive)
        return
    sup.stop_run(ref, 'product job cleanup/recovery')


class FencedWorkspace(WorkspaceSupervisor):
    def __init__(self, root, ctx, **kw):
        super().__init__(root, **kw)
        self.ctx = ctx
        if hasattr(self.sandbox, 'dependency_progress'):
            self.sandbox.dependency_progress = lambda stats: ctx.log('dependency.progress ' + json.dumps(stats))

    def authorize(self, *args, **kwargs):
        self.ctx.queue.verify(self.ctx.lease)
        return super().authorize(*args, **kwargs)

    def _require_active(self, *args, **kwargs):
        self.ctx.queue.verify(self.ctx.lease)
        return super()._require_active(*args, **kwargs)

    def _reserve_command(self, *args, **kwargs):
        self.ctx.queue.reserve(self.ctx.lease, 'tool')
        return super()._reserve_command(*args, **kwargs)

    def _revoked(self, store, generation):
        try:
            self.ctx.queue.verify(self.ctx.lease)
            return self.ctx.cancelled.is_set() or super()._revoked(store, generation)
        except Exception:
            return True


def rebase_onto(broker, candidate, base, dest):
    """Export the new base and re-apply the candidate's own diff (all or nothing). False means a conflict: the
    workspace is the clean new base. Never a merge: the rebased result is a NEW candidate with new review/QA/UAT."""
    import subprocess
    broker.export_commit(base, dest)
    patch = broker._bare('diff', '--no-ext-diff', '--no-textconv', '--no-color', '--binary', '--full-index',
                         candidate.base_sha, candidate.commit_sha)
    if not patch.strip():
        return True
    if len(patch) > 16 * 1024 * 1024:
        return False
    applied = subprocess.run([broker.git, 'apply', '--binary', '--whitespace=nowarn', '-'], cwd=dest, input=patch,
                             env=broker._env(), capture_output=True, timeout=120).returncode == 0
    if not applied:  # git apply is atomic, but start from a pristine base anyway
        fsutil.clear_dir(dest)
        broker.export_commit(base, dest)
    return applied


def pack_tree(root):
    entries = fsutil.scan_tree(Path(root), limits=fsutil.TreeLimits(max_bytes=64 * 1024 * 1024))
    if any(e.kind == 'symlink' for e in entries):
        raise ValueError('build/checkpoint symlinks are unsupported')
    return {e.rel: base64.b64encode((Path(root) / e.rel).read_bytes()).decode()
            for e in entries if e.kind == 'file'}


def unpack_tree(files, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    if not isinstance(files, dict) or len(files) > 20000:
        raise ValueError('invalid source bundle')
    for name, encoded in files.items():
        rel = fsutil.validate_relpath(name)
        raw = base64.b64decode(encoded, validate=True)
        total += len(raw)
        if total > 64 * 1024 * 1024:
            raise ValueError('bundle too large')
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        dest.chmod(0o644)
    root.chmod(0o755)


class ProductWorkspace:
    def __init__(self, db, store, workflow, root, harness, redactor):
        self.db, self.store, self.workflow = db, store, workflow
        self.root, self.harness, self.redactor = Path(root).resolve(), harness, redactor

    def configuration(self, identity):
        with self.db.read() as s:
            p = s.get(Project, identity['project_id'])
            config = p.workflow.get('pipeline')
            if not isinstance(config, dict):
                raise ValueError('project has no approved runner configuration')
            manifest = parse_manifest(config['manifest'])
            base = p.workflow['accepted_tip']
        return manifest, base

    @staticmethod
    def repair_feedback(s, identity):
        return s.scalar(select(Message).where(
            Message.project_id == identity['project_id'], Message.ticket_id == identity['ticket_id'],
            func.json_extract(Message.meta, '$.intent').in_(('repair_feedback', 'rebase_request')),
            func.json_extract(Message.meta, '$.scope_version') == identity['scope_version'])
            .order_by(Message.created_at.desc(), Message.seq.desc(), Message.id.desc()).limit(1))

    def start(self, ctx):
        identity = ctx.queue.verify(ctx.lease)
        manifest, base = self.configuration(identity)
        sup = FencedWorkspace(self.root, ctx)
        if sup.broker(identity['project_id']).accepted_sha() != base:
            raise ValueError('managed accepted Git ref differs from product DB')
        run_id = 'run-' + uuid.uuid4().hex[:12]
        ref = RunRef(identity['project_id'], run_id)
        descriptor = {'kind': 'pipeline_workspace', 'project_id': ref.project_id, 'run_id': ref.run_id,
                      'generation': identity['generation'], 'owner': ctx.tag}
        ctx.queue.register_resource(ctx.lease, descriptor)
        def cleanup():
            cleanup_workspace(sup, ref, ctx.lease.job_id, ctx.lease.generation)
        ctx.add_stopper(cleanup, resource=descriptor)
        started = sup.start_attempt(identity['project_id'], ticket_id=identity['ticket_id'],
            scope_version=identity['scope_version'], role=identity['role'], attempt=identity['attempt'],
            generation=identity['generation'], lease_id=identity['lease_owner'], manifest=manifest,
            run_id=run_id, allow_install_egress=True,
            provenance={'created_by': 'product-job-lease', 'job_id': identity['job_id']})
        candidate, rebased, obsolete_checkpoint = None, False, None
        with self.db.read() as s:
            checkpoint = s.get(Job, identity['job_id']).runtime_ref.get('pipeline_checkpoint')
            if checkpoint:
                saved = json.loads(self.store.read_bytes(s, checkpoint))
                if any(saved.get(k) != identity[k] for k in ('project_id', 'ticket_id', 'scope_version')):
                    raise ValueError('checkpoint belongs to an obsolete scope')
                if saved['base_sha'] != base:
                    # An independent ticket can be accepted while this run waits.
                    # Preserve the old checkpoint as history; never overlay old-base
                    # files on the newly accepted tree.
                    obsolete_checkpoint, checkpoint = checkpoint, None
                else:
                    # Each resume gets a fresh attempt ref and credential; copy only validated source bytes.
                    fsutil.clear_dir(sup.src_dir(ref))
                    unpack_tree(saved['files'], sup.src_dir(ref))
            if not checkpoint and identity['role'] == 'developer':
                # Repair/rebase starts from the last superseded immutable candidate; the accepted ref stays untouched.
                feedback = self.repair_feedback(s, identity)
                candidate = s.get(Candidate, feedback.meta.get('candidate_id')) if feedback else None
                if feedback is not None and candidate is None:
                    raise ValueError('repair feedback references an unavailable candidate')
                if candidate is not None:
                    if (candidate.status != 'superseded' or candidate.scope_version != identity['scope_version']
                            or candidate.ticket_id != identity['ticket_id'] or candidate.project_id != identity['project_id']):
                        raise ValueError('repair candidate is obsolete')
                    fsutil.clear_dir(sup.src_dir(ref))
                    broker = sup.broker(ref.project_id)
                    if candidate.base_sha == base:
                        broker.export_commit(candidate.commit_sha, sup.src_dir(ref))
                    else:
                        rebased = rebase_onto(broker, candidate, base, sup.src_dir(ref))
                    sup._chmod_for_sandbox(sup.src_dir(ref))
        if obsolete_checkpoint:
            with self.db.write() as s:
                ctx.queue.verify_identity(s, identity)
                job = s.get(Job, identity['job_id'])
                job.runtime_ref = {**job.runtime_ref, 'pipeline_checkpoint': None,
                    'obsolete_checkpoints': [*job.runtime_ref.get('obsolete_checkpoints', []), obsolete_checkpoint]}
                self._post(s, identity, 'checkpoint-base-changed:' + identity['job_id'] + ':' + str(identity['generation']),
                    'Accepted base changed. The old checkpoint remains in history; this attempt uses the current base '
                    'and the previous candidate repair/rebase path. New review, QA and UAT are required.',
                    [obsolete_checkpoint], 'checkpoint_base_changed', new_base=base)
        if candidate is not None and candidate.base_sha != base and identity['role'] == 'developer' and not checkpoint:
            with self.db.write() as s:
                self._post(s, identity, 'rebase:' + identity['job_id'] + ':' + str(identity['generation']),
                    ('Candidate changes were re-applied cleanly on the new accepted base; verify and resubmit.' if rebased else
                     'Candidate changes conflict with the new accepted base; the workspace starts from the new base. '
                     'Re-implement the approved scope; the previous diff is in the candidate history.'),
                    [], 'rebase_result', candidate_id=candidate.id, old_base=candidate.base_sha, new_base=base, applied=rebased)
        return sup, started, manifest

    def bootstrap_available(self, identity):
        manifest, base = self.configuration(identity)
        with self.db.read() as s:
            project = s.get(Project, identity['project_id'])
            is_new = project.mode == 'new'
        broker = WorkspaceSupervisor(self.root).broker(identity['project_id'])
        return (is_new and manifest.runner == 'react-vite' and
                not broker._bare('ls-tree', '-r', '--name-only', base).strip())

    def bootstrap(self, ctx, sup, started):
        from .bootstrap import generate_lock, bootstrap_contract
        identity = ctx.queue.verify(ctx.lease)
        if identity['role'] != 'developer' or not self.bootstrap_available(identity):
            raise ValueError('reference bootstrap is only available to a developer on a NEW empty accepted base')
        package = json.loads(sup.read_file(started.ref, started.credential, 'package.json'))
        lock = generate_lock(package)
        # A generated dependency lock is ordinary attempt source, included in diff/commit/review.
        # This write rechecks product lease, workspace ownership and snapshot path boundaries.
        sup.write_file(started.ref, started.credential, 'package-lock.json',
                       (json.dumps(lock, indent=2) + '\n').encode())
        contract = bootstrap_contract()
        ctx.log('reference dependency bootstrap catalog=' + contract['catalog_digest'])
        return {'exit_code': 0, 'generated': 'package-lock.json', 'catalog_digest': contract['catalog_digest'],
                'next': 'Run install, test and build through run_command. Submit the generated lock with the candidate.'}

    def save_checkpoint(self, ctx, sup, started):
        record = sup.checkpoint(started.ref, started.credential, 'Persistent product checkpoint')
        files = {}
        broker = sup.broker(started.ref.project_id)
        temporary = sup.run_dir(started.ref) / ('checkpoint-' + uuid.uuid4().hex)
        temporary.mkdir()
        broker.export_commit(record['sha'], temporary)
        files = pack_tree(temporary)
        with self.db.write() as s:
            identity = ctx.queue.identity(s, ctx.lease)
            artifact = self.store.put_json(s, project_id=identity['project_id'], kind='other', name='checkpoint.json',
                document={**{k: identity[k] for k in ('project_id', 'ticket_id', 'scope_version')},
                          'base_sha': record['base_sha'], 'files': files}, meta={'producer': 'broker'})
            job = s.get(Job, identity['job_id'])
            job.runtime_ref = {**job.runtime_ref, 'pipeline_checkpoint': artifact.id}
            self._post(s, identity, 'checkpoint:' + identity['job_id'] + ':' + uuid.uuid4().hex,
                       'Workspace checkpoint saved', [artifact.id], 'checkpoint')
        return artifact.id

    def _post(self, s, identity, key, body, attachments, intent, **metadata):
        message, _ = append_effect(s, project_id=identity['project_id'], ticket_id=identity['ticket_id'],
            thread_id='ticket:' + identity['ticket_id'], sender='agent:' + identity['role'], recipient='user',
            body=self.redactor.redact(body)[:7900], idempotency_key=key, attachment_ids=attachments,
            meta=self.redactor.redact_value({'intent': intent, 'scope_version': identity['scope_version'],
                'job_id': identity['job_id'], 'generation': identity['generation'], 'fake': identity['fake'], **metadata}))
        return message

    def suite(self, identity):
        with self.db.read() as s:
            rows = list(s.scalars(select(Message).where(Message.ticket_id == identity['ticket_id'])
                                  .order_by(Message.seq.desc())))
            row = next((m for m in rows if m.meta.get('intent') == 'qa_plan' and
                        m.meta.get('scope_version') == identity['scope_version']), None)
            if not row:
                raise ValueError('no persisted QA suite for current scope')
            suite = QaPlan.model_validate(json.loads(self.store.read_bytes(s, row.attachment_ids[-1])))
            version = s.query(TicketVersion).filter_by(ticket_id=identity['ticket_id'], version=identity['scope_version']).one()
            suite.check_criteria(version.uac)
            return suite, row.attachment_ids[-1]

    def submit(self, ctx, sup, started, manifest, message):
        record = sup.submit_candidate(started.ref, started.credential, message)
        identity = ctx.queue.verify(ctx.lease)
        suite, suite_id = self.suite(identity)
        built, build_files, build_error = None, None, ''
        try:
            built = sup.build_target(started.ref, record['sha'])
            build_files = pack_tree(sup.run_dir(started.ref) / 'builds' / built['build_id'] / 'artifact')
        except Exception as exc:
            ctx.queue.verify(ctx.lease)
            built, build_files = None, None
            build_error = self.redactor.redact(str(exc))[:2000]
        gate = None
        if built:
            source = sup.run_dir(started.ref) / 'verify' / built['build_id'] / 'src'
            gate = self.run_gate(ctx, sup, started, manifest, source)
            with self.db.read() as s:
                baseline = next((m for m in s.scalars(select(Message).where(Message.ticket_id == identity['ticket_id'])
                    .order_by(Message.seq.desc())) if m.meta.get('intent') == 'baseline_evidence' and
                    m.meta.get('scope_version') == identity['scope_version'] and m.meta.get('base_sha') == record['base_sha']), None)
                if baseline:
                    base_report = json.loads(self.store.read_bytes(s, baseline.meta['baseline_artifact_id']))
                    required = set((base_report.get('gate') or {}).get('executed_test_ids', []))
                    missing = required - set(gate.get('executed_test_ids', []))
                    if missing:
                        gate.update(status='incomplete', missing_baseline_tests=sorted(missing))
        runner = self.harness.identity() if built else None
        with self.db.write() as s:
            identity = ctx.queue.identity(s, ctx.lease)
            t = s.get(Ticket, identity['ticket_id'])
            commit = self.store.put_git_commit(s, project_id=t.project_id, sha=record['sha'])
            source_attempt = {k: identity[k] for k in ('job_id', 'generation', 'scope_version')}
            receipt = self.store.put_json(s, project_id=t.project_id, kind='report', name='commit-receipt.json',
                document={'kind': 'candidate_commit', 'project_id': t.project_id, 'ticket_id': t.id,
                    'commit_artifact_id': commit.id, 'commit_sha': record['sha'], 'base_sha': record['base_sha'],
                    'source_attempt': source_attempt}, meta={'producer': 'broker'})
            workflow = bind_service(self.workflow, s)
            candidate = workflow.submit_candidate(ctx.actor(), t.id, t.revision,
                Attempt(identity['job_id'], identity['generation'], identity['scope_version']),
                commit_artifact_id=commit.id, commit_receipt_id=receipt.id, base_sha=record['base_sha'],
                submission_key='pipeline-submit:' + identity['root_job_id'])
            command_reports = self.command_reports(s, identity, sup.run_dir(started.ref) / 'evidence')
            gate_artifact = self.store.put_json(s, project_id=t.project_id, kind='report', name='repo-gates.json',
                document={'candidate_id': candidate.id, 'build_error': build_error, 'gate': gate,
                    'commands': command_reports}, meta={'producer': 'verification'})
            attachments = [receipt.id, suite_id, gate_artifact.id]
            attachments += [r[k] for r in command_reports for k in ('stdout_file_artifact_id', 'stderr_file_artifact_id')]
            if built:
                site = sup.run_dir(started.ref) / 'builds' / built['build_id'] / 'artifact'
                bundle = self.store.put_json(s, project_id=t.project_id, kind='other', name='build-files.json',
                    document=build_files, meta={'producer': 'builder'})
                build = self.store.put_json(s, project_id=t.project_id, kind='build_record', name='build.json',
                    document={**built, 'bundle_artifact_id': bundle.id, 'gate_artifact_id': gate_artifact.id},
                    meta={'producer': 'builder'})
                target = self.store.put_json(s, project_id=t.project_id, kind='target_manifest', name='target.json',
                    document={'project_id': t.project_id, 'ticket_id': t.id, 'candidate_id': candidate.id,
                        'scope_version': t.current_version, 'source_sha': candidate.commit_sha, 'base_sha': candidate.base_sha,
                        'build_artifact_id': build.id, 'build_digest': built['build_digest'],
                        'runner_manifest_digest': suite.digest, 'suite_artifact_id': suite_id,
                        'toolchain_digest': digest_of(built['toolchain']), 'config_digest': digest_of(manifest.effective_config()),
                        'fixture_digest': digest_of(manifest.fixture), 'migration_digest': digest_of(manifest.migrations),
                        'runner': runner, 'execution_manifest': manifest.to_dict(), 'node_image_id': built['toolchain']['image_id'],
                        'gate_artifact_id': gate_artifact.id}, meta={'producer': 'builder', 'source_attempt': source_attempt})
                workflow.attach_target(Actor('service:builder', 'builder', t.project_id), t.id, t.revision,
                    candidate.id, build_artifact_id=build.id, target_artifact_id=target.id, target_digest=target.checksum)
                attachments += [bundle.id, build.id, target.id]
            self._post(s, identity, 'candidate:' + identity['root_job_id'],
                'Candidate submitted; technical review and separate QA execution are required.', attachments,
                'candidate_handoff', candidate_id=candidate.id, build_error=build_error, gate=gate)
            published = {'candidate_id': candidate.id, 'commit_sha': record['sha'], 'build_error': build_error, 'gate': gate,
                'pipeline_completion': {'job_id': identity['job_id'], 'generation': identity['generation']}}
            bind_service(ctx.queue, s).complete(ctx.lease, published)
        return {'candidate_id': candidate.id, 'commit_sha': record['sha'], 'build_error': build_error, 'gate': gate}

    def command_reports(self, s, identity, directory):
        records = []
        for path in sorted(directory.glob('cmd-*.json')):
            record = json.loads(path.read_text())
            for key in ('stdout_file', 'stderr_file'):
                log = directory / record[key]
                artifact = self.store.put_bytes(s, project_id=identity['project_id'], kind='log',
                    name=key + '.log', data=self.redactor.redact(log.read_text(errors='replace')).encode(),
                    meta={'producer': 'verification'})
                record[key + '_artifact_id'] = artifact.id
            records.append(record)
        return records

    def run_gate(self, ctx, sup, started, manifest, source):
        spec, _, local = sup._require_active(started.ref)
        command = manifest.commands['test']
        seq, generation = sup._reserve_command(local, spec, ctx.lease.generation)
        image = sup.sandbox.image_id(manifest.image)
        result = sup.sandbox.run(name=sup.sandbox.container_name(spec.project_id, spec.run_id, generation),
            image=image, source=source, argv=list(command.argv), network=command.network, limits=spec.limits,
            env=manifest.env, labels=sup._labels(spec, generation), timeout_s=command.timeout_s,
            is_cancelled=lambda: sup._revoked(local, generation))
        report = sup._record_command(local.dir / 'evidence', seq, 'required-repo-test', spec, generation,
                                     manifest, result, image_id=image)
        return {**node_gate(result.stdout, result.stderr, result.exit_code),
            'environment_digest': digest_of({'image': image, 'manifest': manifest.effective_config()}),
            'infrastructure_failure': result.timed_out or result.cancelled or result.oom_killed or result.truncated,
            'exit_code': result.exit_code, 'command': report}

    def base_build(self, ctx):
        """Run the same pinned commands on accepted base, in a fresh sandbox without target report access."""
        identity = ctx.queue.verify(ctx.lease)
        sup, started, manifest = self.start(ctx)
        source = sup.run_dir(started.ref) / 'baseline' / 'src'
        source.mkdir(parents=True, mode=0o777)
        sup.broker(identity['project_id']).export_commit(started.spec.base_sha, source)
        entries = fsutil.scan_tree(source)
        if not any(e.kind == 'file' for e in entries):
            return {'applicable': False, 'reason': 'initial empty technical base', 'base_sha': started.spec.base_sha}
        sup._chmod_for_sandbox(source)
        spec, _, local = sup._require_active(started.ref)
        image = sup.sandbox.image_id(manifest.image)
        result = {'applicable': True, 'base_sha': started.spec.base_sha, 'status': 'incomplete', 'source': source}
        for phase in ('install', 'build'):
            cmd = manifest.commands[phase]
            seq, generation = sup._reserve_command(local, spec, ctx.lease.generation)
            execution = sup.sandbox.run(name=sup.sandbox.container_name(spec.project_id, spec.run_id, generation),
                image=image, source=source, argv=list(cmd.argv), network=cmd.network, limits=spec.limits, env=manifest.env,
                labels=sup._labels(spec, generation), timeout_s=cmd.timeout_s,
                is_cancelled=lambda: sup._revoked(local, generation))
            sup._record_command(local.dir / 'evidence', seq, 'baseline-' + phase, spec, generation, manifest, execution, image_id=image)
            if execution.exit_code != 0:
                result['error'] = 'baseline ' + phase + ' failed'
                break
        else:
            result.update(status='built', site=source / manifest.build_output,
                          gate=self.run_gate(ctx, sup, started, manifest, source))
        with self.db.write() as s:
            identity = ctx.queue.identity(s, ctx.lease)
            records = self.command_reports(s, identity, local.dir / 'evidence')
            attachments = [r[k] for r in records for k in ('stdout_file_artifact_id', 'stderr_file_artifact_id')]
            artifact = self.store.put_json(s, project_id=identity['project_id'], kind='report', name='baseline-build.json',
                document={k: v for k, v in result.items() if k not in ('source', 'site')} | {'commands': records},
                meta={'producer': 'verification'})
            attachments.append(artifact.id)
            gate = result.get('gate')
            if gate and gate['status'] == 'failed' and not gate['infrastructure_failure']:
                result['fingerprint_artifact_ids'] = []
                for failure in gate.get('failures', [gate]):
                    fp = self.store.put_json(s, project_id=identity['project_id'], kind='report', name='baseline-failure.json',
                        document={'kind': 'baseline_failure', 'ticket_id': identity['ticket_id'], 'scope_version': identity['scope_version'],
                            'base_sha': result['base_sha'], 'category': 'baseline', 'uac_ids': [], 'infrastructure_failure': False,
                            'test_id': failure['test_id'], 'signature': failure['signature'], 'environment_digest': gate['environment_digest'],
                            'environment': {'image_id': image, 'manifest_digest': manifest.digest}}, meta={'producer': 'verification'})
                    attachments.append(fp.id)
                    result['fingerprint_artifact_ids'].append(fp.id)
                if result['fingerprint_artifact_ids']:
                    result['fingerprint_artifact_id'] = result['fingerprint_artifact_ids'][0]
            self._post(s, identity, 'baseline:' + identity['job_id'] + ':' + str(identity['generation']),
                'Accepted base checks: ' + result['status'] + '. Baseline failures require an exact user waiver or a fix.',
                attachments, 'baseline_evidence', base_sha=result['base_sha'], baseline_artifact_id=artifact.id)
        result['artifact_id'] = artifact.id
        return result

"""Owned temporary runtime directories, with durable cleanup and pinned diagnostics."""
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

from sqlalchemy import select

from app.persistence import append_message
from app.persistence.models import Job, Message
from app.workers.runtime import reap_recorded_processes
from app.workspace.fsutil import read_file_beneath, validate_relpath
from app.workspace.runspec import atomic_write_json

MARKER = '.runtime-owner.json'
MAX_LOG_BYTES = 64 * 1024 * 1024


def _directory(path, *, private=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    forbidden = 0o077 if private else 0o022
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & forbidden):
        raise ValueError('runtime directory ownership or permissions mismatch')
    return True


def _path(root, resource):
    job_id = resource['job_id']
    generation = resource['generation']
    if (len(validate_relpath(job_id).parts) != 1 or type(generation) is not int or generation < 1
            or resource['owner'] != f'{job_id}:{generation}'
            or resource['purpose'] not in ('verification', 'hermes')):
        raise ValueError('invalid runtime directory descriptor')
    return Path(root) / job_id / str(generation)


def allocate_directory(ctx, root, purpose, *, store, redactor, finalizers):
    resource = {'kind': 'pipeline_directory', 'purpose': purpose,
                'project_id': ctx.job['project_id'], 'job_id': ctx.lease.job_id,
                'generation': ctx.lease.generation, 'owner': ctx.tag,
                'allocation_id': uuid.uuid4().hex}
    root = Path(root)
    directory = _path(root, resource)
    # Persist the creation intent before touching the filesystem. Unknown or
    # pre-existing contents never become ours merely because the path matches.
    def stop():
        cleanup_directory(root, resource, db=ctx.queue.db, store=store, redactor=redactor)
    finalizers.append(ctx.add_finalizer(stop, resource=resource))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _directory(root)
    directory.parent.mkdir(exist_ok=True, mode=0o700)
    _directory(directory.parent)
    directory.mkdir(mode=0o700)
    atomic_write_json(directory / MARKER, resource)
    return directory


def _owned_job(s, resource):
    job = s.get(Job, resource['job_id'])
    if (job is None or job.project_id != resource['project_id']
            or (job.runtime_ref.get('cleanup') or {}).get('generation') != resource['generation']
            or resource not in job.runtime_ref.get('resources', [])):
        raise ValueError('runtime archive does not belong to the cleaning attempt')
    return job


def _archive_logs(directory, resource, *, db, store, redactor):
    with db.read() as s:
        _owned_job(s, resource)
    if not reap_recorded_processes({'id': resource['job_id'], 'runtime_ref': {'resources': [
            {'kind': 'process_groups', 'generation': resource['generation']}]}}):
        raise ValueError('runtime processes must be gone before diagnostic cleanup')
    # This private configuration is deleted with the directory, never archived.
    # It lets crash recovery redact the already revoked per-run relay bearer too.
    try:
        config = json.loads(read_file_beneath(directory, 'worker.json', max_bytes=MAX_LOG_BYTES))
    except FileNotFoundError:
        config = {}
    redactor = redactor.with_secrets(config.get('relay_token', ''))
    with db.write() as s:
        job = _owned_job(s, resource)
        key = f"runtime-archive:{resource['job_id']}:{resource['generation']}:{resource['allocation_id']}"
        if s.scalar(select(Message.id).where(Message.project_id == job.project_id,
                                            Message.idempotency_key == key)):
            return
        attachments = []
        for name in ('transport.log', 'conversation-result.json'):
            try:
                data = read_file_beneath(directory, name, max_bytes=MAX_LOG_BYTES)
            except FileNotFoundError:
                continue
            artifact = store.put_bytes(s, project_id=job.project_id, kind='log', name=name,
                data=redactor.redact(data.decode(errors='replace')).encode(), run_id=job.id,
                meta={'producer': 'pipeline-runtime', 'generation': resource['generation']})
            attachments.append(artifact.id)
        if attachments:
            # Message attachments are durable pins even after the job finishes.
            append_message(s, project_id=job.project_id, ticket_id=job.ticket_id,
                thread_id=f"job:{job.id}:g{resource['generation']}", sender='system:supervisor',
                body='Runtime diagnostics archived before temporary directory cleanup.',
                idempotency_key=key, attachment_ids=attachments,
                meta={'runtime_log': True, 'intent': 'runtime_archive', 'generation': resource['generation']})


def cleanup_directory(root, resource, *, db, store, redactor):
    root = Path(root)
    directory = _path(root, resource)
    if not _directory(root) or not _directory(directory.parent) or not _directory(directory, private=True):
        return
    try:
        marker = json.loads(read_file_beneath(directory, MARKER, max_bytes=16384))
    except FileNotFoundError:
        directory.rmdir()  # Only an empty creation intent is safe without its marker.
        return
    if marker != resource:
        raise ValueError('runtime directory marker differs from its durable owner')
    if resource['purpose'] == 'hermes':
        _archive_logs(directory, resource, db=db, store=store, redactor=redactor)
    # Delete only after diagnostics commit. shutil.rmtree does not follow child
    # symlinks; the private generation directory itself was checked with lstat.
    shutil.rmtree(directory)
    try:
        directory.parent.rmdir()
    except OSError:
        pass  # Another generation may still own this job's parent directory.

"""Pinned Hermes child with a private home, exact tool surface, and a product-owned relay."""
import json
import os
import sys
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlsplit, unquote
from app.runtime_spike.relay import Relay
from app.workers.runtime import WaitingForInput, reap_recorded_processes
from .relay import ProductAdmission
from .files import allocate_directory
from .transcript import TranscriptProjection

WORKER = Path(__file__).resolve().parents[1] / 'runtime_spike' / 'hermes_worker.py'


class HermesDriver:
    def __init__(self, python, home_root, client, *, store=None):
        # Resolving a venv's python symlink selects the base interpreter and loses installed Hermes.
        self.python, self.root, self.client = Path(python).absolute(), Path(home_root).resolve(), client
        self.store = store
        if not self.python.is_file():
            raise ValueError('HERMES_PYTHON must name the pinned Hermes virtualenv interpreter')

    def validate_install(self):
        from app.runtime_spike.preflight import PIN
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TZ')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
        script = ("import importlib.metadata,json,sys; d=importlib.metadata.distribution('hermes-agent'); "
            "print(json.dumps({'python':list(sys.version_info[:3]),'version':d.version,"
            "'direct_url':json.loads(d.read_text('direct_url.json') or '{}')}))")
        probe = subprocess.run([str(self.python), '-I', '-c', script], env=env, capture_output=True, timeout=15)
        try:
            metadata = json.loads(probe.stdout) if probe.returncode == 0 else {}
            version = tuple(metadata.get('python', []))
            direct = metadata.get('direct_url', {})
            url = urlsplit(direct.get('url', ''))
            if not ((3, 11) <= version[:2] < (3, 14) and metadata.get('version') == PIN['package_version']
                    and direct.get('dir_info', {}).get('editable') is True and url.scheme == 'file' and not url.netloc):
                raise ValueError('metadata mismatch')
            source = Path(unquote(url.path))
            head = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], env=env, capture_output=True, timeout=15)
            dirty = subprocess.run(['git', '-C', str(source), 'status', '--porcelain'], env=env, capture_output=True, timeout=15)
            if head.returncode or dirty.returncode or head.stdout.decode().strip() != PIN['commit'] or dirty.stdout.strip():
                raise ValueError('Hermes source pin/cleanliness mismatch')
            return {'python': '.'.join(map(str, version)), 'package_version': metadata['version'], 'commit': PIN['commit']}
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('Hermes installation must be the clean pinned editable checkout with Python >=3.11,<3.14') from exc

    def run(self, ctx, identity, snapshot, tools, parameters):
        finalizers = []
        try:
            return self._run(ctx, identity, snapshot, tools, parameters, finalizers)
        finally:
            for finish in reversed(finalizers):
                finish()

    def _run(self, ctx, identity, snapshot, tools, parameters, finalizers):
        if self.store is None:
            raise ValueError('Hermes driver requires the product artifact store for diagnostic cleanup')
        installation = self.validate_install()
        role = identity['role']
        config, provider = self.client.registry.for_role(role), self.client.provider_for(role)
        with ctx.queue.db.write() as s:
            from app.persistence.models import Job
            ctx.queue.verify_identity(s, identity)
            job = s.get(Job, identity['job_id'])
            job.runtime_ref = {**job.runtime_ref, 'pipeline_runtime': installation,
                'pipeline_models': [*job.runtime_ref.get('pipeline_models', []),
                    {'generation': identity['generation'], 'provider': config.provider, 'model': config.model,
                     'context_artifact_id': snapshot.artifact_id}]}
        if provider.fake or identity['fake'] or not hasattr(provider, '_key'):
            raise ValueError('Hermes HTTP driver requires a real configured provider; fake tests use an injected driver')
        directory = allocate_directory(ctx, self.root, 'hermes', store=self.store,
            redactor=self.client.redactor, finalizers=finalizers)
        home = directory / 'home'
        home.mkdir(mode=0o700)
        (home / 'config.yaml').write_text(json.dumps({'agent': {'api_max_retries': 1, 'auto_recovery_cycles': 0},
            'tools': {'tool_search': {'enabled': 'off'}}, 'memory': {'memory_enabled': False, 'user_profile_enabled': False},
            'compression': {'enabled': False, 'context_length': 262144}}))
        admission = ProductAdmission(ctx, self.client.redactor)
        runtime_result, waiting, collected, collector_error = {}, [], [], []
        def wrapped(name, handler):
            def call(arguments):
                try:
                    return self.client.redactor.redact_value(handler(arguments))
                except WaitingForInput as exc:
                    waiting.append(exc)
                    return {'status': 'waiting_input'}
            return call
        relay = Relay(admission, ctx.lease.job_id, ctx.lease.generation, config.model, provider._key,
                      {name: wrapped(name, handler) for name, handler in tools.items()},
                      request_projection=TranscriptProjection(ctx.log) if role == 'developer' else None,
                      provider_retry_delays=(5, 15, 30))
        relay.ENDPOINT = provider.base_url + '/chat/completions'
        proc, thread = None, None
        try:
            with relay:
                worker_config = {'relay_url': relay.url, 'relay_token': relay.token, 'model': config.model,
                    'tool_names': list(tools), 'tool_parameters': parameters, 'tool_prefix': 'pipeline_',
                    'toolset': 'product_pipeline', 'max_iterations': ctx.job['limits']['model_calls'] or sys.maxsize,
                    'completion_tool': 'propose_tests' if role == 'qa' else 'submit_candidate',
                    'output_tokens': self.client.output_limit(role, ctx.job['limits'].get('output_tokens')),
                    'session_id': ctx.tag, 'system': snapshot.system + '\nUse only pipeline tools. Paths are relative to the project source snapshot, never the private runtime cwd. Historical completed tool exchanges may be projected to digest summaries; '
                    'these are old observations, not file contents. Current scope, feedback, user decisions and recent exchanges are preserved. '
                    'Read current source if an archived detail is needed.',
                    'prompt': snapshot.user}
                path = directory / 'worker.json'
                path.write_text(json.dumps(worker_config))
                path.chmod(0o600)
                env = {k: v for k, v in os.environ.items() if k in
                    ('PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
                env.update(HOME=str(home), HERMES_HOME=str(home), XDG_CONFIG_HOME=str(home),
                    PYTHONUNBUFFERED='1', PYTHONNOUSERSITE='1', AIAGENTS_RUN=ctx.tag)
                ctx.queue.register_resource(ctx.lease, {'kind': 'process_groups', 'generation': ctx.lease.generation})
                proc = subprocess.Popen([str(self.python), str(WORKER), str(path)], cwd=directory, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                ctx.register_process(proc.pid)
                def collect():
                    count = 0
                    try:
                        for line in iter(lambda: proc.stdout.readline(65537), b''):
                            count += len(line)
                            if len(line) > 65536 or count > 2 * 1024 * 1024:
                                collector_error.append('Hermes event transport exceeded its bound')
                                proc.terminate()
                                break
                            text = self.client.redactor.redact(line.decode(errors='replace'))
                            collected.append(text)
                            if line.startswith(b'DEV006_EVENT '):
                                event = json.loads(text[13:])
                                if event['kind'] == 'runtime.result':
                                    runtime_result.update(event['payload'])
                    except Exception as exc:
                        collector_error.append(str(exc)[:300])
                thread = threading.Thread(target=collect, daemon=True)
                thread.start()
                while proc.poll() is None:
                    if waiting or admission.error or ctx.cancelled.is_set():
                        proc.terminate()
                        break
                    ctx.queue.verify(ctx.lease)
                    time.sleep(0.05)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                thread.join(timeout=5)
        finally:
            admission.close()
            # A lease can be revoked inside the polling loop. Even on that
            # exception path, stop the labelled child before collecting its
            # final output or allowing its directory finalizer to run.
            clean = reap_recorded_processes({'id': identity['job_id'], 'runtime_ref': {'resources': [
                {'kind': 'process_groups', 'generation': identity['generation']}]}})
            if proc is not None and clean:
                proc.wait(timeout=5)
            if thread is not None:
                thread.join(timeout=5)
            (directory / 'transport.log').write_text(''.join(collected))
            if not clean or thread is not None and thread.is_alive():
                raise RuntimeError('Hermes process/collector cleanup is incomplete')
            # The relay bearer is revoked now. Cleanup reads it only to redact
            # diagnostics, then deletes the private config with the directory.
        if waiting:
            raise waiting[0]
        if admission.error:
            raise admission.error
        if collector_error:
            raise RuntimeError('; '.join(collector_error))
        ctx.queue.verify(ctx.lease)
        if not runtime_result or runtime_result.get('error'):
            if admission.provider_failure:
                from app.agents.models import ProviderUnavailable, ProviderRejected
                code = admission.provider_failure.get('http_status')
                failure = ProviderRejected if type(code) is int and 400 <= code < 500 and code != 408 else ProviderUnavailable
                error = failure('Hermes provider request failed' + (f' (HTTP {code})' if code is not None else ' (transport)'))
                error.http_status = code
                raise error
            raise RuntimeError('Hermes failed: ' + str(runtime_result.get('error', 'no final event'))[:500])
        return runtime_result

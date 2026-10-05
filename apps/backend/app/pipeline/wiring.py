"""Production wiring; POSIX/Docker required only when opting into the pipeline worker."""
import os
from pathlib import Path
from .scheduler import PipelineScheduler


def build_pipeline(structured, store, workflow, queue, *, env=None):
    env = os.environ if env is None else env
    if os.name != 'posix':
        raise ValueError('pipeline execution requires Linux/macOS; use WSL on Windows')
    from app.workspace import WorkspaceSupervisor
    from .workspace import ProductWorkspace
    from .harness import DockerHarness
    from .hermes import HermesDriver
    from .runtime import PipelineRuntime
    root = Path(env.get('PIPELINE_WORKSPACE_ROOT', 'data/pipeline-workspaces')).resolve()
    supervisor = WorkspaceSupervisor(root)
    harness = DockerHarness(supervisor.sandbox, image=env.get('PIPELINE_RUNNER_IMAGE', 'aiagent-verification:1.63.0'))
    if not supervisor.sandbox.available():
        raise ValueError('Docker engine is unavailable')
    harness.identity()  # fail early if the pinned runner image has not been built
    driver = HermesDriver(env.get('HERMES_PYTHON', ''), root / '.hermes', structured.client)
    driver.validate_install()
    workspace = ProductWorkspace(structured.db, store, workflow, root, harness, structured.redactor)
    runtime = PipelineRuntime(structured, workspace, driver)
    return runtime, PipelineScheduler(structured.db, queue, workflow, runtime=runtime.name)


def build_preview(db, store, *, env=None):
    """Preview supervisor for the same worker. Docker is required; owner is stable per host so a restart reconciles."""
    env = os.environ if env is None else env
    if os.name != 'posix':
        raise ValueError('previews require Linux/macOS; use WSL on Windows')
    from app.preview.service import PreviewService
    root = Path(env.get('PIPELINE_WORKSPACE_ROOT', 'data/pipeline-workspaces')).resolve()
    service = PreviewService(db, store, root)
    if not service.sandbox.available():
        raise ValueError('Docker engine is unavailable')
    return service

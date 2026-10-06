"""Worker process: the persistent job supervisor (DEV-004).

    python -m app.worker [--runtime none|fake|structured|pipeline] [--db PATH] [--worker-id ID]

With --runtime none (default) the worker reconciles leases and quota waits but claims no
work. --runtime pipeline wires structured PO/lead plus pinned Hermes developer/QA.
--runtime fake runs the LABELLED fake
runtime for local dry runs; its results are never real provider or QA evidence.
"""
import argparse
import os
import signal
import threading
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--runtime", choices=("none", "fake", "structured", "pipeline", "onboarding"), default=os.getenv("WORKER_RUNTIME", "none"))
    parser.add_argument("--worker-id")
    parser.add_argument("--lease-s", type=float, default=float(os.getenv("WORKER_LEASE_S", "30")))
    args = parser.parse_args(argv)

    from app.config import ARTIFACT_DIR, DATABASE_PATH, pipeline_workspace_root
    from app.domain import Workflow
    from app.persistence import ArtifactStore, Database, migrate
    from app.workers import JobQueue, ProviderLimiter, Supervisor, WorkerConfig

    db_path, artifact_dir = args.db or DATABASE_PATH, args.artifacts or ARTIFACT_DIR
    if not Path(db_path).exists() or migrate.current_revision(db_path) != migrate.head_revision():
        print(f"Database {db_path} belum di revisi terbaru; jalankan: python -m app.persistence upgrade", flush=True)
        return 2

    db, store = Database(db_path), ArtifactStore(artifact_dir)
    workflow = Workflow(db, store)
    queue = JobQueue(db, lease_s=args.lease_s, startable=workflow.startable)
    runtimes = {}
    if args.runtime == "fake":
        from app.adapters.runtime.fake import FakeRuntime
        runtimes["fake"] = FakeRuntime()
        print("PERINGATAN: runtime FAKE aktif; hasilnya bukan bukti provider/QA nyata.", flush=True)
    maintenance = []
    previews = integrator = None
    if args.runtime in ('onboarding', 'pipeline'):
        if os.name != 'posix':
            raise ValueError('onboarding execution requires Linux/macOS; use WSL on Windows')
        from app.onboarding.runtime import OnboardingRuntime
        from app.agents import Redactor
        root = pipeline_workspace_root()
        redactor = Redactor([v for k, v in os.environ.items() if any(
            marker in k.upper() for marker in ('API_KEY', 'SECRET', 'TOKEN', 'PASSWORD'))])
        runtimes['onboarding'] = OnboardingRuntime(db, store, root, redactor)
        from app.release.runtime import ReleaseRuntime
        runtimes['release'] = ReleaseRuntime(db, store, workflow, root, redactor)
    if args.runtime in ("structured", "pipeline"):
        from app.agents.wiring import build_structured_runtime
        runtime, threads, notes = build_structured_runtime(db, store, workflow, queue)
        runtimes["structured"] = runtime
        maintenance.append(threads.ensure_reply_jobs)
        for note in notes:
            print(f"PERINGATAN: {note}.", flush=True)
        if args.runtime == "pipeline":
            from app.pipeline.wiring import build_pipeline
            pipeline, scheduler = build_pipeline(runtime, store, workflow, queue)
            runtimes[pipeline.name] = pipeline
            from app.pipeline.setup import NewProjectSetup
            setup = NewProjectSetup(db, queue, root, runtime.redactor)
            runtimes[setup.name] = setup
            from app.pipeline.prefetch import ReferencePrefetch
            prefetch = ReferencePrefetch(db, store, root, runtime.redactor)
            runtimes[prefetch.name] = prefetch
            maintenance.append(setup.tick)
            maintenance.append(scheduler.tick)
            from app.pipeline.wiring import build_integrator, build_preview
            previews = build_preview(db, store)
            maintenance.append(previews.tick)
            integrator = build_integrator(db, store, workflow)
            maintenance.append(integrator.tick)
    config = WorkerConfig(**({"worker_id": args.worker_id} if args.worker_id else {}),
                          heartbeat_s=max(0.5, args.lease_s / 3))
    supervisor = Supervisor(db, store, runtimes, queue=queue, limiter=ProviderLimiter(), config=config,
                            workflow=workflow)
    supervisor.maintenance.extend(maintenance)

    stopped = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        print(f"Worker menerima sinyal {signal.Signals(signum).name}; berhenti...", flush=True)
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    claims = ", ".join(runtimes) or "tidak ada (hanya rekonsiliasi lease/quota)"
    print(f"Worker siap ({config.worker_id}); runtime: {claims}.", flush=True)
    try:
        supervisor.run_forever(stopped)
    finally:
        if previews is not None:
            previews.shutdown()
        if integrator is not None:
            integrator.shutdown()
        db.dispose()
    print("Worker berhenti dengan tertib.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

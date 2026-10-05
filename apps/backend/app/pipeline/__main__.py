"""Explicit local operator setup of a NEW managed project; never imports or executes an original repo."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['configure'])
    parser.add_argument('--project', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--workspace-root', type=Path, required=True)
    parser.add_argument('--db', type=Path)
    args = parser.parse_args()
    from app.config import DATABASE_PATH, ARTIFACT_DIR
    from app.domain import Actor, Workflow
    from app.persistence import Database, ArtifactStore, apply_change, EventSpec
    from app.persistence.models import Project
    from app.workspace import WorkspaceSupervisor
    from app.workspace.manifest import parse_manifest
    manifest = parse_manifest(json.loads(args.manifest.read_text(encoding='utf-8')))
    db, store = Database(args.db or DATABASE_PATH), ArtifactStore(ARTIFACT_DIR)
    workspace = WorkspaceSupervisor(args.workspace_root)
    with db.read() as s:
        p = s.get(Project, args.project)
        if p is None or p.mode != 'new':
            raise ValueError('DEV-010 setup supports only an existing NEW project row; existing repos require DEV-013')
    path = args.workspace_root.resolve() / p.id / 'repo.git'
    base = workspace.broker(p.id).accepted_sha() if path.exists() else workspace.create_project(p.id)
    workflow = Workflow(db, store)
    if not p.workflow.get('accepted_tip'):
        workflow.initialize_base(Actor('operator:setup', 'integrator', p.id), p.revision, base)
    elif p.workflow['accepted_tip'] != base:
        raise ValueError('DB/Git accepted base differs; setup cannot reset it')
    with db.write() as s:
        p = s.get(Project, p.id)
        apply_change(s, Project, p.id, expected_revision=p.revision,
            values={'workflow': {**p.workflow, 'pipeline': {'manifest': manifest.to_dict()}}},
            event=EventSpec('project.pipeline_configured', 'operator:setup', {'manifest_digest': manifest.digest}))
    print('Pipeline configured. Scope approval is still required before execution.')
    db.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

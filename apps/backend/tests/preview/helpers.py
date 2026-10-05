"""Verified-target fixtures for preview tests: real DB, artifact store and Docker; synthetic trusted receipts.

The candidates here reach UAT through the real domain transitions with contract fixtures for QA evidence (the same
approach as tests/domain), because a preview only needs a stored, digest-pinned build and a passed verification.
Nothing here is model output or a real QA run.
"""
from __future__ import annotations

import socket
import subprocess
import tempfile
from pathlib import Path

from app.persistence import append_message
from app.persistence.models import Candidate
from app.pipeline.workspace import pack_tree, unpack_tree
from app.workspace import fsutil
from tests.domain.conftest import HASH, World

NODE_IMAGE = "node:22.20.0-alpine"
INDEX = ('<!doctype html><title>Coffee preview</title><main id="app">Latte 4.00</main>'
         '<script src="/assets/app.js"></script>')
SITE = {"index.html": INDEX, "assets/app.js": "document.title = document.title + ' ready';"}


def docker_ready() -> bool:
    try:
        probe = subprocess.run(["docker", "image", "inspect", NODE_IMAGE], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def bundle_of(files: dict[str, str]) -> tuple[dict, str]:
    """(stored bundle document, build digest) exactly as the pipeline records them."""
    with tempfile.TemporaryDirectory() as raw:
        source, copy = Path(raw) / "source", Path(raw) / "copy"
        for name, content in files.items():
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            (source / name).write_text(content, encoding="utf-8")
        packed = pack_tree(source)
        unpack_tree(packed, copy)
        return packed, fsutil.sha256_tree(copy, fsutil.scan_tree(copy))


class PreviewEnv:
    def __init__(self, db, store, tmp_path):
        self.db, self.store, self.tmp_path = db, store, tmp_path
        self.world = World(db, store)

    def target(self, t, c, files, bundle_files=None, **override):
        w = self.world
        packed, digest = bundle_of(files)
        if bundle_files is not None:  # stored bytes that differ from the digest the target pins (tamper fixture)
            packed = bundle_of(bundle_files)[0]
        with self.db.write() as s:
            bundle = self.store.put_json(s, project_id=t.project_id, kind="other", name="build-files.json",
                document=packed, meta={"producer": "builder"})
            build = self.store.put_json(s, project_id=t.project_id, kind="build_record", name="build.json",
                document={"build_digest": digest, "bundle_artifact_id": bundle.id}, meta={"producer": "builder"})
            document = {"project_id": t.project_id, "ticket_id": t.id, "candidate_id": c.id,
                "scope_version": t.current_version, "source_sha": c.commit_sha, "base_sha": c.base_sha,
                "build_artifact_id": build.id, "build_digest": digest,
                **{k: HASH for k in ("runner_manifest_digest", "toolchain_digest", "config_digest", "fixture_digest",
                                     "migration_digest")},
                "execution_manifest": {"fixture": {"id": "coffee-menu-v1"}, "migrations": {"id": "none"}},
                "node_image_id": subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", NODE_IMAGE],
                    capture_output=True, check=True).stdout.decode().strip(), **override}
            current = s.get(Candidate, c.id)
            target = self.store.put_json(s, project_id=t.project_id, kind="target_manifest", name="target.json",
                document=document, meta={"producer": "builder", "source_attempt": current.integration["source_attempt"]})
            # The pipeline attaches the bundle to the candidate hand-off message, which is what pins it.
            append_message(s, project_id=t.project_id, ticket_id=t.id, thread_id="ticket:" + t.id, sender="agent:developer",
                kind="system", body="Candidate submitted", attachment_ids=[bundle.id, build.id, target.id],
                meta={"intent": "candidate_handoff", "candidate_id": c.id})
        w.w.attach_target(w.actor("builder"), t.id, w.ticket(t.id).revision, c.id,
                          build_artifact_id=build.id, target_artifact_id=target.id, target_digest=target.checksum)
        return target, bundle

    def to_uat(self, t=None, files=None, **override):  # override may include bundle_files=...
        """Candidate -> reviewed -> verified -> UAT. Returns (ticket, candidate, target, bundle artifact)."""
        w = self.world
        t, c = w.submitted(t)
        target, bundle = self.target(t, c, files or SITE, **override)
        lead, ref = w.job(t, "technical-lead")
        w.w.approve_review(lead, t.id, w.ticket(t.id).revision, ref, c.id)
        _, qa = w.job(t, "qa")
        v, smoke = w.proof(t, c, target)
        w.w.open_uat(w.actor("verification"), t.id, w.ticket(t.id).revision, qa, c.id, v.id, smoke.id)
        return w.ticket(t.id), c, target, bundle

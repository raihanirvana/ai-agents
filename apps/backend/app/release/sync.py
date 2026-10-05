"""Combined synchronisation of a release onto a newer HEAD of the user's source repository.

The user's repository is only READ. The release's own changes (base..tip) are re-applied onto the new source HEAD in a
scratch worktree of the managed repository, producing ONE replacement release candidate. It gets its own build target,
combined regression and approval; tickets whose files overlap the source drift are marked as affected, and only their
manual UAC form the checklist. Earlier ticket acceptance stays as history, UAC are never changed here (a UAC change is
a ticket revision with its own scope approval), and the accepted ref of the managed repository is not moved.
"""
import json
import subprocess
import uuid
from pathlib import Path

from app.persistence import append_message
from app.persistence.models import Project, Release
from app.workspace.gitbroker import ATTEMPT_PREFIX, is_sha
from .export import export_base


def changed_files(broker, a, b) -> set[str]:
    out = broker._bare("diff", "--name-only", "-z", a, b)
    return {p.decode("utf-8", "replace") for p in out.split(b"\0") if p}


def sync_release(rt, ctx, identity, payload):
    from app.onboarding.source import fetch_source_head
    from app.workspace import WorkspaceSupervisor
    from .runtime import ReleaseBlocked
    pid = identity["project_id"]
    manifest = rt._manifest(payload)
    with rt.db.read() as s:
        release = s.get(Release, payload["release_id"])
        project = s.get(Project, pid)
        if release is None or release.project_id != pid or release.status not in ("draft", "approved"):
            raise ReleaseBlocked("only a draft or approved release that was not exported can be synchronised")
        target = json.loads(rt.store.read_bytes(s, release.target_artifact_id))
        old_scope = release.scope_snapshot
        entries = target.get("regression_scope", old_scope)
        old_tip, rid = release.accepted_tip, release.id
        source_path = project.repo_ref
        detail = project.workflow.get("onboarding_detail") or {}
    sup = WorkspaceSupervisor(rt.root)
    broker = sup.broker(pid)
    old_source = (target.get("sync") or {}).get("source_new") or detail.get("source_sha")
    old_base = export_base(rt, project, release, target)
    if not (is_sha(old_source or "") and is_sha(old_base)):
        raise ReleaseBlocked("the release has no recorded source base to synchronise from")
    # A retry shares its root job and therefore the same immutable source/candidate pins. A different operation
    # gets separate refs: failed verification must not permanently lock the original release out of synchronisation.
    operation = identity["root_job_id"]
    source_ref = f"refs/releases/{rid}-{operation}-source"
    sync_ref = f"refs/releases/{rid}-{operation}-sync"
    refs = broker.refs()
    new_source = refs.get(source_ref)
    candidate = refs.get(sync_ref)
    if new_source is None:
        inspected = ctx.tool_call("fetch_source_head", lambda: fetch_source_head(
            sup, pid, source_path, source_ref, check=lambda: ctx.queue.verify(ctx.lease)))
        new_source = inspected["source_sha"]
    if new_source == old_source:
        raise ReleaseBlocked("the source repository is still at the base this release was built on; nothing to synchronise")

    patch = broker._bare("diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-color", old_base, old_tip)
    if len(patch) > 16 * 1024 * 1024:
        raise ReleaseBlocked("the release patch is too large to re-apply automatically")
    scratch = sup.root / pid / "sync" / uuid.uuid4().hex[:12]
    attempt_ref = ATTEMPT_PREFIX + "run-" + uuid.uuid4().hex[:12]
    scratch.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if candidate is None:
        ctx.queue.verify(ctx.lease)
        broker.create_worktree(attempt_ref, scratch, new_source)
        try:
            applied = subprocess.run([broker.git, "apply", "--binary", "--whitespace=nowarn", "-"], cwd=scratch, input=patch,
                                     env=broker._env(), capture_output=True, timeout=180)
            if applied.returncode != 0:
                detail_text = applied.stderr.decode(errors="replace").strip()[:400]
                raise ReleaseBlocked("the release changes conflict with the new source HEAD and cannot be re-applied "
                                     "automatically; resolve it with a new ticket. Git said: " + detail_text)
            ctx.queue.verify(ctx.lease)
            candidate, changed = broker.commit_worktree(scratch, attempt_ref, new_source,
                "Release synchronisation onto the new source HEAD", author=("Release Sync", "release-sync@localhost"))
        finally:
            broker.remove_worktree(scratch)
            broker._bare("update-ref", "-d", attempt_ref, check=False)
        if not changed:
            raise ReleaseBlocked("the new source HEAD already contains every release change; nothing to synchronise")
        ctx.queue.verify(ctx.lease)
        broker._bare("update-ref", sync_ref, candidate, "0" * 40)

    drift = changed_files(broker, old_base, new_source)
    affected = []
    new_entries = []
    for entry in entries:
        files = changed_files(broker, entry["integrated_sha"] + "^", entry["integrated_sha"]) \
            if broker._bare("rev-parse", "--verify", "--quiet", entry["integrated_sha"] + "^", check=False).strip() else set()
        hit = bool(files & drift)
        manual = [f"{entry['ticket_id']}:{u['id']}" for u in entry["uac"] if u.get("mode") == "manual"]
        new_entries.append({**entry, "affected_by_sync": hit, "overlap_files": sorted(files & drift)[:20],
                            "checklist": manual if hit else []})
        if hit:
            affected.append(entry["ticket_id"])
    drift_patch = broker._bare("diff", "--binary", "--full-index", "--no-ext-diff", "--no-color", old_base, new_source)
    combined_patch = broker._bare("diff", "--binary", "--full-index", "--no-ext-diff", "--no-color", new_source, candidate)
    sync_info = {"replaces": rid, "source_old": old_source, "source_new": new_source, "candidate_sha": candidate,
                 "affected_tickets": affected, "drift_file_count": len(drift)}
    with rt.db.write() as s:
        ctx.queue.verify_identity(s, identity)
        drift_art = rt.store.put_bytes(s, project_id=pid, kind="other", name="sync-source-drift.patch", data=drift_patch,
                                       meta={"producer": "verification", "release_id": rid})
        diff_art = rt.store.put_bytes(s, project_id=pid, kind="other", name="sync-combined.patch", data=combined_patch,
                                      meta={"producer": "verification", "release_id": rid})
        receipt = rt.store.put_json(s, project_id=pid, kind="report", name="release-sync.json", meta={"producer": "verification"},
            document={"kind": "release_sync", **sync_info, "source_drift_patch_artifact_id": drift_art.id,
                      "combined_patch_artifact_id": diff_art.id, "drift_files": sorted(drift)[:200],
                      "scope_digest": rt.workflow.scope_digest(new_entries), "old_scope_digest": rt.workflow.scope_digest(old_scope)})
    result = rt.verify_commit(ctx, identity, commit_sha=candidate, entries=new_entries, manifest=manifest, accepted_tip=candidate,
        extra_target={"export_base": new_source, "sync": sync_info,
                      "technical_review_evidence_ids": [drift_art.id, diff_art.id]}, label="release-sync")
    return rt._publish(ctx, identity, new_entries, candidate, result, replaces=rid, sync_candidate=True,
                       extra_evidence=[receipt.id, drift_art.id, diff_art.id])

"""Explicit local export of an approved release: a patch and a Git bundle. Never push, open a PR, or deploy."""
import hashlib
import json
import uuid

from app.domain import Actor
from app.persistence import append_message
from app.persistence.columns import utcnow
from app.persistence.models import Project, Release
from app.persistence.transactions import bind_service
from app.workers.runtime import Outcome
from app.workspace import WorkspaceSupervisor
from app.workspace.gitbroker import is_sha

MAX_PATCH_BYTES = 64 * 1024 * 1024


def export_base(rt, project, release, target) -> str:
    """The commit the user's own repository already contains: a synchronised release names it, an onboarded
    repository uses its original source HEAD, a new project the root. The managed baseline can include an explicit
    onboarding patch that the source does not contain, so that patch must be part of the export too."""
    if target.get("export_base"):
        return target["export_base"]
    baseline = (project.workflow.get("onboarding_detail") or {}).get("source_sha")
    return baseline or ""


def source_head_changed(rt, broker, project, target):
    """None when the user's source repository is where onboarding left it; otherwise (expected, observed)."""
    if project.mode != "existing" or not project.repo_ref:
        return None
    from app.onboarding.source import inspect_source
    expected = ((target.get("sync") or {}).get("source_new")
                or (project.workflow.get("onboarding_detail") or {}).get("source_sha"))
    observed = inspect_source(broker, project.repo_ref)[0]["source_sha"]
    return None if observed == expected else (expected, observed)


def export_release(rt, ctx, identity, payload):
    from .runtime import ReleaseBlocked
    pid = identity["project_id"]
    with rt.db.read() as s:
        release = s.get(Release, payload["release_id"])
        project = s.get(Project, pid)
        if release is None or release.project_id != pid or release.status != "approved":
            raise ReleaseBlocked("only an approved release can be exported")
        target = json.loads(rt.store.read_bytes(s, release.target_artifact_id))
        revision, tip = release.revision, release.accepted_tip
    sup = WorkspaceSupervisor(rt.root)
    broker = sup.broker(pid)
    ctx.queue.verify(ctx.lease)
    if not (is_sha(tip) and broker._bare("cat-file", "-t", tip, check=False).strip() == b"commit"):
        raise ReleaseBlocked("the frozen release commit is missing from the managed repository")
    changed = source_head_changed(rt, broker, project, target)
    if changed:
        raise ReleaseBlocked(f"the source repository HEAD changed ({changed[0][:12] if changed[0] else '?'} -> {changed[1][:12]}) since "
                             "this release was built; export is refused. Synchronise the release onto the new base "
                             "(new combined verification and approval) instead of exporting a stale one.")
    base = export_base(rt, project, release, target)
    if not base:  # a new project exports against its root commit: the patch is the whole project
        base = broker._bare("rev-list", "--max-parents=0", tip).decode().split()[-1]
    if not is_sha(base) or broker._bare("cat-file", "-t", base, check=False).strip() != b"commit":
        raise ReleaseBlocked("the export base commit is not available")
    patch = broker._bare("diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-color", base, tip)
    if len(patch) > MAX_PATCH_BYTES:
        raise ReleaseBlocked("the release patch exceeds the export size limit")
    if not patch.strip():
        raise ReleaseBlocked("the release does not change anything relative to the export base")
    ref = "refs/releases/" + release.id
    existing = broker.refs().get(ref)
    if existing not in (None, tip):
        raise ReleaseBlocked("the release export ref already points elsewhere; refusing to move it")
    if existing is None:
        broker._bare("update-ref", ref, tip, "0" * 40)
    exports = sup.root / pid / "exports"
    exports.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle_path = exports / f"release-{release.id}-{uuid.uuid4().hex[:8]}.bundle"
    args = ["bundle", "create", str(bundle_path), ref]
    root_commit = broker._bare("rev-list", "--max-parents=0", tip).decode().split()
    if base not in root_commit:  # the user's repository already has the base: ship only what is new
        args.append("^" + base)
    broker._bare(*args)
    broker._bare("bundle", "verify", str(bundle_path))
    bundle = bundle_path.read_bytes()
    bundle_path.unlink()
    if source_head_changed(rt, broker, project, target):
        raise ReleaseBlocked("the source repository HEAD changed during export; synchronise and approve a new release")
    branch = "release/" + release.id[:8]
    result = {"formats": ["patch", "bundle"], "tip": tip, "base_sha": base, "branch": branch, "ref_in_bundle": ref,
              "patch_sha256": hashlib.sha256(patch).hexdigest(), "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
              "pushed": False, "deployed": False, "exported_at": utcnow().isoformat(),
              "how_to_use": f"git apply --index release.patch   or   git fetch release.bundle {ref}:{branch}"}
    actor = Actor("service:release-export", "integrator", pid)
    with rt.db.write() as s:
        ctx.queue.verify_identity(s, identity)
        patch_art = rt.store.put_bytes(s, project_id=pid, kind="other", name=f"release-{release.id[:8]}.patch", data=patch,
                                       meta={"producer": "integrator", "release_id": release.id, "base_sha": base})
        bundle_art = rt.store.put_bytes(s, project_id=pid, kind="other", name=f"release-{release.id[:8]}.bundle", data=bundle,
                                        meta={"producer": "integrator", "release_id": release.id, "base_sha": base})
        result = {**result, "patch_artifact_id": patch_art.id, "bundle_artifact_id": bundle_art.id}
        done = bind_service(rt.workflow, s).record_export(actor, release.id, revision, result)
        append_message(s, project_id=pid, thread_id="release:" + release.id, sender="service:release", recipient="user",
            kind="message", attachment_ids=[patch_art.id, bundle_art.id],
            body="Release exported locally as a patch and a Git bundle. Nothing was pushed, no pull request was opened and nothing was deployed.",
            idempotency_key="release-export:" + identity["root_job_id"],
            meta={"intent": "release_exported", "release_id": release.id})
        completion = {"release_id": done.id, "status": done.status, "pipeline_completion": {
            "job_id": identity["job_id"], "generation": identity["generation"]}}
        bind_service(ctx.queue, s).complete(ctx.lease, completion)
    return Outcome("succeeded", completion)

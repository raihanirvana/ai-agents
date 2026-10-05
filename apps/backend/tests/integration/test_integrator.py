"""DEV-012: accepted-ref integration with real Git refs, fault injection and reconciliation."""
import json
import threading

import pytest
from sqlalchemy import select

from app.domain.types import Conflict
from app.integration.integrator import SimulatedCrash
from app.persistence import RevisionConflict
from app.persistence.models import Approval, Artifact, Candidate, Dependency, Event, Message, Project
from app.workspace.errors import GitBrokerError
from app.workspace.gitbroker import ACCEPTED_REF
from tests.domain.conftest import SCOPE


def tip(env):
    with env.db.read() as s:
        return s.get(Project, env.pid).workflow["accepted_tip"]


def candidate(env, cid):
    with env.db.read() as s:
        return s.get(Candidate, cid)


def evidence(env, ticket_id):
    with env.db.read() as s:
        notes = [m for m in s.scalars(select(Message).where(Message.thread_id == "integration:" + ticket_id))]
        return [json.loads(env.store.read_bytes(s, m.attachment_ids[0])) for m in notes]


def crash_at(point):
    def fault(where, plan):
        if where == point:
            raise SimulatedCrash(point)
    return fault


def test_accept_moves_the_ref_first_and_only_then_records_accepted_and_opens_the_dependency(env):
    w = env.world
    up_sha = env.commit({"menu.js": "export const menu = ['latte'];\n"})
    up, c, target, v, ids = env.to_uat(up_sha)
    down = w.approve(w.new({**SCOPE, "title": "Order", "dependencies": [up.id]}))
    assert not w.w.eligible(w.user, down.id)

    op = env.accept(up, c, target, v, ids)
    assert w.ticket(up.id).phase == "integrating" and op["status"] == "pending"
    assert op["expected_base"] == env.base and op["target_sha"] == up_sha
    # Clicking accept is not acceptance: nothing moved, and the dependency is still closed.
    assert env.broker.accepted_sha() == env.base and tip(env) == env.base
    assert not w.w.eligible(w.user, down.id)

    assert env.integrator().run_once() == [(up.id, "updated")]
    assert env.broker.accepted_sha() == up_sha and tip(env) == up_sha
    accepted = candidate(env, c.id)
    assert w.ticket(up.id).phase == "accepted" and accepted.status == "accepted" and accepted.integrated_sha == up_sha
    assert accepted.integration["status"] == "done"
    assert w.w.eligible(w.user, down.id)
    with env.db.read() as s:
        dep = s.scalar(select(Dependency).where(Dependency.ticket_id == down.id))
        assert (dep.state, dep.accepted_candidate_id, dep.integration_sha) == ("satisfied", c.id, up_sha)
        kinds = [e.type for e in s.scalars(select(Event).order_by(Event.cursor))]
    assert kinds.index("ticket.uat_accepted") < kinds.index("ticket.integrated")
    [report] = evidence(env, up.id)
    assert report["outcome"] == "updated" and report["observed_before"] == env.base and report["observed_after"] == up_sha
    # Retrying the accept or the integration changes nothing.
    t = w.ticket(up.id)
    with pytest.raises(Conflict):
        w.w.accept_uat(w.user, t.id, t.revision, c.id, 1, target.id, target.checksum, v.id, ids)
    assert env.integrator().run_once() == [] and env.broker.accepted_sha() == up_sha


def test_a_crash_before_the_ref_update_resumes_and_after_it_finalises_without_a_second_update(env):
    w = env.world
    first_sha = env.commit({"a.txt": "one\n"})
    t, c, target, v, ids = env.to_uat(first_sha)
    env.accept(t, c, target, v, ids)
    with pytest.raises(SimulatedCrash):
        env.integrator(fault=crash_at("before_ref_update")).run_once()
    assert env.broker.accepted_sha() == env.base and candidate(env, c.id).integration["status"] == "pending"
    assert env.integrator().run_once() == [(t.id, "updated")]
    assert env.broker.accepted_sha() == first_sha and w.ticket(t.id).phase == "accepted"

    second_sha = env.commit({"b.txt": "two\n"})
    t2, c2, target2, v2, ids2 = env.to_uat(second_sha)
    env.accept(t2, c2, target2, v2, ids2)
    with pytest.raises(SimulatedCrash):
        env.integrator(fault=crash_at("after_ref_update")).run_once()
    # Git moved, SQLite did not: not atomic together, and nothing pretends otherwise.
    assert env.broker.accepted_sha() == second_sha and tip(env) == first_sha
    assert w.ticket(t2.id).phase == "integrating" and candidate(env, c2.id).integration["status"] == "pending"
    updates = []
    assert env.integrator(fault=lambda where, plan: updates.append(where)).run_once() == [(t2.id, "recovered")]
    assert updates == []  # the recovery never reached the update-ref step
    assert tip(env) == second_sha and w.ticket(t2.id).phase == "accepted"
    assert evidence(env, t2.id)[-1]["observed_before"] == second_sha


def test_two_accepts_on_the_same_base_integrate_one_and_send_the_other_back_with_its_approval_kept(env):
    w = env.world
    a_sha = env.commit({"a.txt": "A\n"})
    b_sha = env.commit({"b.txt": "B\n"})
    a = env.to_uat(a_sha)
    b = env.to_uat(b_sha)
    env.accept(*a[:3], *a[3:])
    env.accept(*b[:3], *b[3:])
    outcomes = dict(env.integrator().run_once())
    assert outcomes == {a[0].id: "updated", b[0].id: "stale_base"}
    assert env.broker.accepted_sha() == a_sha and tip(env) == a_sha
    bt, bc = w.ticket(b[0].id), candidate(env, b[1].id)
    assert bt.phase == "development" and bc.status == "superseded" and bc.integration["status"] == "diverged"
    with env.db.read() as s:
        approvals = {ap.candidate_id for ap in s.scalars(select(Approval).where(Approval.type == "uat"))}
        rebase = [m.meta for m in s.scalars(select(Message).where(Message.ticket_id == bt.id)) if m.meta.get("intent") == "rebase_request"]
    assert approvals == {a[1].id, b[1].id}  # history, never transferred to a rebased candidate
    assert rebase and rebase[-1]["old_base"] == env.base and rebase[-1]["new_base"] == a_sha


def test_concurrent_accepts_of_one_ticket_create_exactly_one_operation(env):
    t, c, target, v, ids = env.to_uat(env.commit({"a.txt": "A\n"}))
    results, barrier = [], threading.Barrier(2)

    def accept():
        barrier.wait()
        try:
            results.append(env.accept(t, c, target, v, ids)["operation_id"])
        except (Conflict, RevisionConflict) as exc:
            results.append(type(exc).__name__)
    threads = [threading.Thread(target=accept) for _ in range(2)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sorted(r for r in results if r in ("Conflict", "RevisionConflict")) and len(results) == 2
    assert len([r for r in results if r not in ("Conflict", "RevisionConflict")]) == 1
    with env.db.read() as s:
        assert len(list(s.scalars(select(Approval).where(Approval.type == "uat")))) == 1


def test_a_ref_moved_outside_the_integrator_blocks_with_evidence_and_is_neither_reset_nor_adopted(env):
    w = env.world
    t, c, target, v, ids = env.to_uat(env.commit({"a.txt": "A\n"}))
    env.accept(t, c, target, v, ids)
    foreign = env.commit({"evil.txt": "pushed around the integrator\n"})
    env.broker._bare("update-ref", ACCEPTED_REF, foreign, env.base)  # an unauthorized write to accepted
    assert env.integrator().run_once() == [(t.id, "blocked")]
    assert env.broker.accepted_sha() == foreign  # no blind reset
    assert tip(env) == env.base  # and the unknown tip is not adopted as accepted
    current, op = w.ticket(t.id), candidate(env, c.id).integration
    assert current.phase == "integrating" and current.blocker["reason"] == "integration_blocked"
    assert op["status"] == "blocked" and op["observed_tip"] == foreign
    report = evidence(env, t.id)[-1]
    assert report["outcome"] == "blocked" and report["observed_before"] == foreign and report["expected_base"] == env.base
    with env.db.read() as s:
        assert s.get(Artifact, op["evidence_artifact_id"]).meta["producer"] == "integrator"
    assert env.integrator().run_once() == []  # blocked operations are not retried in a loop


def test_a_candidate_that_is_not_a_fast_forward_of_the_base_is_blocked_before_the_ref_moves(env):
    orphan = env.broker._bare("commit-tree", "4b825dc642cb6eb9a060e54bf8d69288fbee4904", "-m", "unrelated root").decode().strip()
    t, c, target, v, ids = env.to_uat(orphan)
    env.accept(t, c, target, v, ids)
    assert env.integrator().run_once() == [(t.id, "blocked")]
    assert env.broker.accepted_sha() == env.base and "fast-forward" in candidate(env, c.id).integration["reason"]


def test_missing_approved_evidence_blocks_the_integration_preflight(env):
    t, c, target, v, ids = env.to_uat(env.commit({"a.txt": "A\n"}))
    env.accept(t, c, target, v, ids)
    with env.db.read() as s:
        path = env.store.resolve(s.get(Artifact, v.evidence_artifact_ids[0]).path)
    path.chmod(0o600)
    path.unlink()
    assert env.integrator().run_once() == [(t.id, "blocked")]
    assert env.broker.accepted_sha() == env.base and "preflight" in candidate(env, c.id).integration["reason"]


def test_a_base_change_during_uat_requires_a_new_candidate_review_qa_and_uat(env):
    w = env.world
    first = env.to_uat(env.commit({"a.txt": "A\n"}))
    waiting = env.to_uat(env.commit({"b.txt": "B\n"}))  # in UAT on the same, soon outdated, base
    env.accept(*first)
    env.integrator().run_once()
    bt, bc = w.ticket(waiting[0].id), candidate(env, waiting[1].id)
    assert bt.phase == "development" and bc.status == "superseded"
    with pytest.raises(Conflict):
        env.accept(*waiting)
    with env.db.read() as s:
        notes = [m.meta for m in s.scalars(select(Message).where(Message.ticket_id == bt.id)) if m.meta.get("intent") == "rebase_request"]
    assert notes[-1]["candidate_id"] == bc.id and notes[-1]["new_base"] == tip(env)
    _, replacement = w.submitted(bt)  # the replacement candidate is built on the new base and must start over
    assert replacement.base_sha == tip(env) and replacement.status == "submitted"


def test_cancel_and_scope_revision_wait_while_integrating(env):
    w = env.world
    t, c, target, v, ids = env.to_uat(env.commit({"a.txt": "A\n"}))
    env.accept(t, c, target, v, ids)
    t = w.ticket(t.id)
    with pytest.raises(Conflict):
        w.w.cancel(w.user, t.id, t.revision)
    with pytest.raises(Conflict):
        w.w.edit_scope(w.user, t.id, t.revision, {**SCOPE, "title": "Changed while integrating"})
    assert env.integrator().run_once() == [(t.id, "updated")]
    assert w.ticket(t.id).phase == "accepted"


def test_the_developer_broker_cannot_write_the_accepted_ref_even_with_a_valid_attempt(env):
    started = env.sup.start_attempt(env.pid, ticket_id="t", scope_version=1, role="developer", attempt=1, generation=1,
                                    lease_id="lease", manifest=env.manifest)
    assert not (env.sup.src_dir(started.ref) / ".git").exists()  # the sandbox snapshot has no Git metadata
    worktree = env.sup.run_dir(started.ref) / "worktree"
    head = env.broker.head(worktree)
    with pytest.raises(GitBrokerError, match="only writes attempt refs"):
        env.broker.commit_worktree(worktree, ACCEPTED_REF, head, "sneak", author=("dev", "dev@localhost"))
    env.sup.write_file(started.ref, started.credential, "x.txt", b"x")
    env.sup.submit_candidate(started.ref, started.credential, "legit attempt commit")
    assert env.broker.accepted_sha() == env.base  # a submitted attempt never moves accepted
    env.sup.stop_run(started.ref, "done")


def test_a_revert_ticket_changes_the_upstream_contract_and_blocks_downstream_until_revalidation(env):
    w = env.world
    up, uc, *rest = env.to_uat(env.commit({"menu.js": "v1\n"}))
    env.accept(up, uc, *rest)
    env.integrator().run_once()
    down = w.approve(w.new({**SCOPE, "title": "Order", "dependencies": [up.id]}))
    assert w.w.eligible(w.user, down.id)
    revert = env.to_uat(env.commit({"menu.js": ""}), document={**SCOPE, "title": "Revert menu", "reverts_candidate_id": uc.id})
    env.accept(*revert)
    assert env.integrator().run_once() == [(revert[0].id, "updated")]
    assert w.ticket(up.id).workflow["contract_change"]["reason"].startswith("accepted candidate reverted")
    with env.db.read() as s:
        dep = s.scalar(select(Dependency).where(Dependency.ticket_id == down.id))
    assert dep.state == "needs_revalidation" and dep.revalidation["trigger_ticket_id"] == up.id
    assert w.ticket(down.id).blocker["reason"] == "dependency_revalidation" and not w.w.eligible(w.user, down.id)

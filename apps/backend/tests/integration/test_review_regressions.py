"""Review probes using real SQLite, artifacts and managed Git refs."""
import pytest

from app.http.queries import candidate as public_candidate
from app.integration.integrator import SimulatedCrash
from app.persistence.models import Candidate, Project
from app.workspace.gitbroker import ACCEPTED_REF
from tests.integration.test_integrator import candidate, crash_at, evidence, tip


def test_unaccepted_candidate_does_not_expose_provenance_as_an_integration_operation(env):
    _, c, *_ = env.to_uat(env.commit({"a.txt": "A"}))
    with env.db.read() as s:
        assert public_candidate(s, s.get(Candidate, c.id))["integration"] is None


@pytest.mark.parametrize("outcome", ["updated", "blocked", "stale_base"])
def test_report_pin_and_terminal_operation_commit_together(env, monkeypatch, outcome):
    item = env.to_uat(env.commit({"a.txt": "A"}))
    if outcome == "stale_base":
        first = env.to_uat(env.commit({"b.txt": "B"}))
        env.accept(*first)
        env.accept(*item)
        env.integrator().integrate(first[0].id)
    else:
        env.accept(*item)
    if outcome == "blocked":
        foreign = env.commit({"foreign.txt": "external"})
        env.broker._bare("update-ref", ACCEPTED_REF, foreign, env.base)
    integrator = env.integrator()
    with monkeypatch.context() as patch:
        patch.setattr(integrator, "_note", lambda *a, **kw: (_ for _ in ()).throw(SimulatedCrash("before pin")))
        with pytest.raises(SimulatedCrash):
            integrator.integrate(item[0].id)
    # Git may have moved, but losing the pin must not leave a terminal DB operation.
    assert candidate(env, item[1].id).integration["status"] == "pending"
    assert env.world.ticket(item[0].id).phase == "integrating"
    expected = "recovered" if outcome == "updated" else outcome
    assert env.integrator().integrate(item[0].id) == expected
    reports = evidence(env, item[0].id)
    assert len(reports) == 1 and reports[0]["outcome"] == expected
    op = candidate(env, item[1].id).integration
    assert op["evidence_artifact_id"]
    with env.db.read() as s:
        from app.persistence.pins import pin_owners
        assert pin_owners(s, op["evidence_artifact_id"])


def test_missing_accepted_ref_is_blocked_with_evidence_instead_of_retrying_silently(env):
    item = env.to_uat(env.commit({"a.txt": "A"}))
    env.accept(*item)
    env.broker._bare("update-ref", "-d", ACCEPTED_REF, env.base)
    assert env.integrator().run_once() == [(item[0].id, "blocked")]
    assert tip(env) == env.base and ACCEPTED_REF not in env.broker.refs()
    assert evidence(env, item[0].id)[0]["outcome"] == "blocked"


def test_priority_edit_does_not_reorder_an_unfinished_git_recovery(env):
    a = env.to_uat(env.commit({"a.txt": "A"}))
    b = env.to_uat(env.commit({"b.txt": "B"}))
    env.accept(*a)
    env.accept(*b)
    with pytest.raises(SimulatedCrash):
        env.integrator(fault=crash_at("after_ref_update")).integrate(a[0].id)
    t = env.world.ticket(a[0].id)
    env.world.w.set_priority(env.world.user, t.id, t.revision, 20)
    outcomes = dict(env.integrator().run_once())
    assert outcomes == {a[0].id: "recovered", b[0].id: "stale_base"}
    assert env.world.ticket(b[0].id).phase == "development"


def test_out_of_order_reconciliation_waits_for_the_operation_that_moved_git(env):
    a = env.to_uat(env.commit({"a.txt": "A"}))
    b = env.to_uat(env.commit({"b.txt": "B"}))
    env.accept(*a)
    env.accept(*b)
    with pytest.raises(SimulatedCrash):
        env.integrator(fault=crash_at("after_ref_update")).integrate(a[0].id)
    assert env.integrator().integrate(b[0].id) == "retry"
    assert candidate(env, b[1].id).integration["status"] == "pending"
    assert env.integrator().integrate(a[0].id) == "recovered"
    assert env.integrator().integrate(b[0].id) == "stale_base"


@pytest.mark.parametrize("invalid", ["ancestry", "db_base"])
def test_recovery_does_not_accept_an_inconsistent_target_ref(env, invalid):
    sha = (env.broker._bare("commit-tree", "4b825dc642cb6eb9a060e54bf8d69288fbee4904", "-m", "orphan").decode().strip()
           if invalid == "ancestry" else env.commit({"a.txt": "A"}))
    item = env.to_uat(sha)
    env.accept(*item)
    env.broker._bare("update-ref", ACCEPTED_REF, sha, env.base)
    if invalid == "db_base":
        with env.db.write() as s:
            project = s.get(Project, env.pid)
            project.workflow = {**project.workflow, "accepted_tip": "f" * 40}
    assert env.integrator().run_once() == [(item[0].id, "blocked")]
    assert env.world.ticket(item[0].id).phase == "integrating"
    assert env.broker.accepted_sha() == sha

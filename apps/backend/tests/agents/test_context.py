"""Context builder: layers, limits, snapshot/hash, decisions, summaries, isolation and resume from the database."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.agents import ContextBuilder, ContextLimits, ContextRefused, ContextTooLarge, Redactor, load_agents
from app.persistence import ArtifactStore, Database, append_message
from app.persistence.models import Artifact, Message, Project
from app.persistence.pins import pinned_artifacts
from app.workers import StaleLease
from tests.domain.conftest import SCOPE

from .conftest import LIMITS, SECRET


def identity(env, ctx):
    return env.queue.verify(ctx.lease)


def build(env, ctx, **kw):
    kw.setdefault("task", {"name": "t"})
    return env.builder.build(identity(env, ctx), **kw)


def say(env, ticket_id, body, *, thread="t", sender="user", kind="message", **kw):
    with env.db.write() as s:
        seq = (s.scalar(select(func.max(Message.seq)).where(Message.thread_id == thread)) or 0) + 1
        s.add(Message(project_id=env.project.id, thread_id=thread, seq=seq, sender=sender, kind=kind, body=body,
                      ticket_id=ticket_id, **kw))


def lead(env, ticket):
    return env.ctx(env.job("technical-lead", "technical_plan", ticket=ticket, stage="plan"))


def po(env, ticket=None):
    return env.ctx(env.job("po", "breakdown", ticket=ticket))


def test_layers_follow_the_architecture_order_and_the_run_identity_comes_last(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    say(env, ticket.id, "Please keep prices in cents.")
    ctx = lead(env, ticket)
    snap = build(env, ctx, task={"name": "technical_plan"}, repo_refs=[{"path": "src/cart.js", "sha": "a" * 40,
                                                                          "snippet": "export const total = 1"}])
    text, system = snap.user, snap.system
    assert system.startswith("# Technical Lead") and "technical_plan" in system  # SOUL + instructions
    order = [text.index(marker) for marker in ("## Scope", "## Project", "## Repository references",
                                                 "## Recent messages", "## Task", "## Run")]
    assert order == sorted(order)
    assert "APPROVED by the user" in text and "UAC-1" in text and "Please keep prices in cents." in text
    assert f"job={ctx.lease.job_id}" in text.split("## Run")[1] and "generation=" in text.split("## Run")[1]
    assert snap.manifest["estimated"] is True and snap.manifest["agent_digest"] == load_agents()["technical-lead"].digest


def test_snapshot_is_stored_hashed_attached_to_the_job_and_pinned(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = lead(env, ticket)
    snap = build(env, ctx, lease=ctx.lease, queue=env.queue)
    job = env.get(ctx.lease.job_id)
    assert job.context_artifact_id == snap.artifact_id
    with env.db.read() as s:
        artifact = s.get(Artifact, snap.artifact_id)
        stored = json.loads(env.store.read_bytes(s, artifact.id))
        assert artifact.kind == "context" and artifact.meta["sha256"] == snap.sha256
        assert stored["sha256"] == snap.sha256 and stored["user"] == snap.user and stored["manifest"] == snap.manifest
        assert snap.artifact_id in pinned_artifacts(s)  # an active job keeps its context


def test_unapproved_scope_is_visible_to_po_but_other_roles_refuse_to_start(agent_env):
    env = agent_env
    draft = env.world.new(SCOPE)  # scope_review, not approved
    assert "NOT approved" in build(env, po(env, draft)).user
    for role, stage in (("technical-lead", "plan"), ("developer", "reply"), ("qa", "reply")):
        ctx = env.ctx(env.job(role, "x", ticket=draft, stage=stage))
        with pytest.raises(ContextRefused, match="has approved"):
            build(env, ctx)


def test_ticket_roles_need_a_ticket_and_a_current_scope_version(agent_env):
    env = agent_env
    ctx = env.ctx(env.job("technical-lead", "plan"))
    with pytest.raises(ContextRefused, match="has none"):
        build(env, ctx)
    ticket = env.approved_ticket()
    ctx = lead(env, ticket)
    ident = identity(env, ctx)
    with pytest.raises(ContextRefused, match="no longer current"):  # a lease that is valid but a scope that moved on
        env.builder.build({**ident, "scope_version": 99}, task={"name": "t"})
    t = env.world.ticket(ticket.id)
    env.world.w.edit_scope(env.world.user, t.id, t.revision, {**SCOPE, "title": "Changed", "uac": [{"id": "U9", "text": "n"}]})
    with pytest.raises(StaleLease):  # the domain revoked the attempt, so even the identity no longer verifies
        build(env, ctx)


def test_only_accepted_decisions_enter_project_knowledge(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    ident = identity(env, lead(env, ticket))
    accepted = env.threads.propose_decision(ident, title="Use cents", rationale="No float money", key="d1")
    rejected = env.threads.propose_decision(ident, title="Use floats", rationale="Simpler", key="d2")
    env.threads.propose_decision(ident, title="Use Redux", rationale="Maybe", key="d3")  # still pending
    env.threads.decide(env.world.user, accepted, True)
    env.threads.decide(env.world.user, rejected, False)
    snap = build(env, lead(env, ticket))
    knowledge = snap.user.split("## Accepted project decisions")[1].split("##")[0]
    assert "Use cents" in knowledge and "Use floats" not in knowledge and "Use Redux" not in knowledge
    assert "Use Redux" in snap.user.split("## Recent messages")[1]  # a pending proposal shows only as a labelled message
    with env.db.read() as s:
        assert [m.id for m in env.threads.accepted_decisions(s, env.project.id)] == [accepted]


def test_proposals_in_the_ticket_thread_are_labelled_not_authoritative(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    say(env, ticket.id, "{...}", thread="plan", sender="agent:technical-lead", meta={"intent": "technical_plan"})
    snap = build(env, lead(env, ticket))
    assert "PROPOSAL, not authoritative" in snap.user


def test_dependency_pins_are_part_of_the_context(agent_env):
    env = agent_env
    upstream, _ = env.world.accepted()
    down = env.world.approve(env.world.new({**SCOPE, "dependencies": [upstream.id]}))
    snap = build(env, lead(env, down))
    layer = snap.user.split("## Dependencies")[1].split("##")[0]
    assert upstream.id in layer and "accepted v1 candidate" in layer and "[satisfied]" in layer


def test_token_limits_trim_oldest_messages_first_and_report_the_gap(agent_env):
    env = agent_env
    env.builder.limits = ContextLimits(total_tokens=8000, messages_tokens=300)
    ticket = env.approved_ticket()
    for n in range(30):
        say(env, ticket.id, f"message number {n:02d} " + "x" * 200)
    snap = build(env, lead(env, ticket))
    layer = next(l for l in snap.manifest["layers"] if l["name"] == "recent_messages")
    kept = [item["seq"] for item in layer["items"]]
    assert kept and kept == sorted(kept) and kept[-1] == 30  # the newest survive
    assert layer["estimated_tokens"] <= 300 + 10 and "message number 29" in snap.user and "message number 00" not in snap.user
    omitted = [g for g in snap.manifest["gaps"] if g["layer"] == "recent_messages"]
    assert len(omitted) == 30 - len(kept) and all(g["reason"] == "token_limit" for g in omitted)
    with env.db.read() as s:  # nothing was deleted from the real history
        assert s.scalar(select(func.count()).select_from(Message).where(Message.ticket_id == ticket.id)) == 30


def test_role_scope_and_task_are_never_dropped_and_too_large_is_an_error(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    env.builder.limits = ContextLimits(total_tokens=300)  # far below the SOUL + instructions
    with pytest.raises(ContextTooLarge, match="limit 300"):
        build(env, lead(env, ticket))
    env.builder.limits = ContextLimits(total_tokens=2500, messages_tokens=100000)
    for n in range(40):
        say(env, ticket.id, "y" * 400)
    snap = build(env, lead(env, ticket))
    assert "## Scope" in snap.user and snap.estimated_tokens <= 2500 and "## Task" in snap.user


def test_a_valid_summary_stands_in_for_trimmed_messages_without_a_gap(agent_env):
    env = agent_env
    env.builder.limits = ContextLimits(total_tokens=8000, messages_tokens=250)
    ticket = env.approved_ticket()
    for n in range(20):
        say(env, ticket.id, f"early discussion {n:02d} " + "z" * 150, thread="chat")
    summary_id = env.threads.record_summary(project_id=env.project.id, thread_id="chat", from_seq=1, to_seq=10,
                                            text="Users want a menu with three drinks.", author="system:summariser",
                                            key="sum-1")
    snap = build(env, lead(env, ticket))
    assert "Users want a menu with three drinks." in snap.user
    layer = next(l for l in snap.manifest["layers"] if l["name"] == "history_summary")
    assert layer["items"][0]["id"] == summary_id and len(layer["items"][0]["message_ids"]) == 10
    uncovered = [g for g in snap.manifest["gaps"] if g["layer"] == "recent_messages"]
    assert all(g["omitted"]["seq"] > 10 for g in uncovered)  # only the unsummarised ones are gaps
    with env.db.read() as s:  # the summary is an addition; the 10 originals are all still there
        assert s.scalar(select(func.count()).select_from(Message).where(Message.thread_id == "chat",
                                                                       Message.kind == "message")) == 20


def test_a_summary_that_no_longer_matches_its_sources_is_not_used_and_is_reported(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    say(env, ticket.id, "original", thread="chat")
    with env.db.write() as s:
        original = s.scalar(select(Message).where(Message.thread_id == "chat"))
        append_message(s, project_id=env.project.id, thread_id="chat", sender="system:summariser", kind="system",
                       body="forged summary", ticket_id=ticket.id,
                       meta={"intent": "summary", "from_seq": 1, "to_seq": 1, "message_ids": [original.id],
                             "source_digest": "0" * 64})
    env.builder.limits = ContextLimits(total_tokens=8000, messages_tokens=1)
    snap = build(env, lead(env, ticket))
    assert "forged summary" not in snap.user
    assert any(g["reason"] == "summary_does_not_match_history" for g in snap.manifest["gaps"])


def test_other_projects_never_leak_into_the_context(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    with env.db.write() as s:
        other = Project(name="Other", mode="new", brief="OTHER-PROJECT-SECRET-BRIEF")
        s.add(other)
        s.flush()
        append_message(s, project_id=other.id, thread_id="x", sender="user", body="OTHER-PROJECT-MESSAGE")
    snap = build(env, lead(env, ticket))
    assert "OTHER-PROJECT" not in snap.user and "OTHER-PROJECT" not in snap.system


def test_secrets_in_the_brief_or_messages_never_reach_the_snapshot(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    with env.db.write() as s:
        s.get(Project, env.project.id).brief = f"Our key is {SECRET}"
    say(env, ticket.id, f"password: hunter2hunter2 and {SECRET}")
    ctx = lead(env, ticket)
    snap = build(env, ctx, lease=ctx.lease, queue=env.queue)
    assert SECRET not in snap.user and "hunter2hunter2" not in snap.user
    with env.db.read() as s:
        assert SECRET not in env.store.read_bytes(s, snap.artifact_id).decode()


def test_resume_rebuilds_from_persistence_after_a_restart(agent_env, db_path, tmp_path):
    env = agent_env
    ticket = env.approved_ticket()
    say(env, ticket.id, "Remove the row at zero?", kind="input_request", sender="agent:developer")
    ctx = lead(env, ticket)
    before = build(env, ctx)
    reopened = Database(db_path)  # a new process: no in-memory state from the old builder
    try:
        fresh = ContextBuilder(reopened, ArtifactStore(env.store.root), load_agents(), Redactor([SECRET]))
        after = fresh.build(identity(env, ctx), task={"name": "t"})
        assert after.sha256 == before.sha256 and after.user == before.user
        answered = fresh.build(identity(env, ctx), task={"name": "t"}, answer="Yes, remove it.")
        assert "Yes, remove it." in answered.user and "valid, resume from your checkpoint" in answered.user
        assert answered.sha256 != before.sha256 and answered.prefix_sha256 == before.prefix_sha256
    finally:
        reopened.dispose()


def test_the_stable_prefix_survives_new_messages_but_not_new_scope(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    first = build(env, lead(env, ticket))
    say(env, ticket.id, "something new happened")
    second = build(env, lead(env, ticket))
    assert second.prefix_sha256 == first.prefix_sha256 and second.sha256 != first.sha256
    with env.db.write() as s:
        s.get(Project, env.project.id).brief = "A different brief"
    assert build(env, lead(env, ticket)).prefix_sha256 != first.prefix_sha256


def test_repo_references_are_bounded_validated_and_runtime_transcript_is_flagged(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    snap = build(env, lead(env, ticket), repo_refs=[{"path": "a.js", "snippet": "q" * 20000},
                                                    {"artifact_id": "some-artifact"}])
    assert snap.user.count("q") <= 1600
    with pytest.raises(ContextRefused):
        build(env, lead(env, ticket), repo_refs=[{"snippet": "no path"}])
    assert any(g["layer"] == "runtime_transcript" for g in snap.manifest["gaps"])


def test_a_single_huge_message_is_cut_not_allowed_to_take_the_budget(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    say(env, ticket.id, "w" * 50000)
    snap = build(env, lead(env, ticket))
    assert "[cut]" in snap.user and snap.user.count("w") <= 1300


def test_messages_with_identical_timestamps_keep_their_thread_order(agent_env):
    """Regression: created_at ties (coarse clocks) must never reorder a conversation; seq decides."""
    from datetime import datetime, timezone
    env = agent_env
    ticket = env.approved_ticket()
    same_instant = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    with env.db.write() as s:
        for seq, ident in ((1, "ffff"), (2, "aaaa"), (3, "cccc"), (4, "bbbb")):  # ids sort the opposite way
            s.add(Message(id=ident * 8, project_id=env.project.id, thread_id="th", seq=seq, sender="user",
                          kind="message", body=f"turn {seq}", ticket_id=ticket.id, created_at=same_instant))
    snap = build(env, lead(env, ticket))
    positions = [snap.user.index(f"turn {n}") for n in (1, 2, 3, 4)]
    assert positions == sorted(positions)
    layer = next(l for l in snap.manifest["layers"] if l["name"] == "recent_messages")
    assert [item["seq"] for item in layer["items"]] == [1, 2, 3, 4]


def test_supervisor_log_lines_never_enter_the_model_context(agent_env):
    """Regression (DEV-008 review): run logs are stored as messages but are not conversation."""
    env = agent_env
    ticket = env.approved_ticket()
    dev = env.ctx(env.job("developer", "implement", ticket=ticket, stage="development", lane="execution",
                          runtime="fake"))
    dev.log("tool read_file")
    dev.log("context abc ~1200 tokens (estimate), 1 gaps")
    say(env, ticket.id, "Please keep prices in cents.")
    snap = build(env, lead(env, ticket))
    assert "Please keep prices in cents." in snap.user  # real conversation is still there
    assert "system:supervisor" not in snap.user and "tool read_file" not in snap.user
    layer = next(l for l in snap.manifest["layers"] if l["name"] == "recent_messages")
    assert len(layer["items"]) == 1

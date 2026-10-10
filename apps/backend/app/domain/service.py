"""Transactional workflow commands. No Git, model, network or process side effects."""
from sqlalchemy import select
from app.persistence import (Database, ArtifactStore, EventSpec, append_event, apply_change,
                             NotFound, RevisionConflict, append_message)
from app.persistence.artifacts import canonical_json, sha256_bytes
from app.persistence.columns import new_id, utcnow
from app.persistence.models import (Project, Ticket, TicketVersion, Approval, Dependency,
    Message, Job, Candidate, Release, Artifact, ACTIVE_JOB_STATUSES)
from .types import ApprovalItem, Attempt, Forbidden, Invalid, Conflict
from . import evidence


class Workflow:
    def __init__(self, db: Database, store: ArtifactStore):
        self.db, self.store = db, store

    def create_project(self, actor, *, name, mode, brief="", repo_ref=None, qa_profile="lightweight"):
        if actor.role != "user" or not name.strip() or mode not in ("new", "existing"):
            raise Invalid("user project name and valid mode required")
        if qa_profile not in ("lightweight", "manual"):
            raise Invalid("Invalid user QA profile")
        if mode == "existing" and not repo_ref:
            raise Invalid("existing project requires a repository reference")
        with self.db.write() as s:
            project = Project(id=actor.project_id, name=name, mode=mode, brief=brief, repo_ref=repo_ref,
                              workflow={"onboarding": "pending", "qa_profile": qa_profile})
            s.add(project)
            s.flush()
            self._event(s, actor, "project.created", project, {"mode": mode})
            return project

    def update_brief(self, actor, expected_revision, brief):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            project = self._row(s, Project, actor.project_id, actor)
            return apply_change(s, Project, project.id, expected_revision=expected_revision,
                values={"brief": brief, "brief_version": project.brief_version + 1},
                event=EventSpec("project.brief_changed", actor.id))

    def set_qa_profile(self, actor, expected_revision, qa_profile):
        with self.db.write() as s:
            self._permit(s, actor, 'user')
            project = self._row(s, Project, actor.project_id, actor)
            if qa_profile not in ('lightweight', 'manual'):
                raise Invalid('Invalid user QA profile')
            return apply_change(s, Project, project.id, expected_revision=expected_revision,
                values={'workflow': {**project.workflow, 'qa_profile': qa_profile}},
                event=EventSpec('project.qa_profile_changed', actor.id, {'qa_profile': qa_profile}))

    def set_priority(self, actor, ticket_id, expected_revision, priority):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision)
            if type(priority) is not int or not -10000 <= priority <= 10000:
                raise Invalid("priority must be a bounded integer")
            return self._change(s, actor, t, "priority_changed", priority=priority)

    def _row(self, s, model, identity, actor):
        row = s.get(model, identity)
        if row is None:
            raise NotFound(model.__tablename__, identity)
        project = row.id if model is Project else row.project_id
        if project != actor.project_id:
            raise Forbidden("cross-project command")
        return row

    def _permit(self, s, actor, *roles):
        if not actor.id or actor.role not in roles:
            raise Forbidden("actor cannot perform this intent")
        self._row(s, Project, actor.project_id, actor)
        if actor.role in ("po", "technical-lead", "developer", "qa", "ui-ux"):
            job = self._row(s, Job, actor.job_id, actor)
            if (job.status != "running" or job.lease_generation != actor.generation or
                job.lease_owner != actor.id or not job.lease_expires_at or job.lease_expires_at <= utcnow() or
                (job.runtime_ref or {}).get("role") != actor.role):
                raise Forbidden("agent binding/lease/generation is inactive")
        return actor

    def _ticket(self, s, actor, tid, revision, phases=None):
        t = self._row(s, Ticket, tid, actor)
        if type(revision) is not int or t.revision != revision:
            raise RevisionConflict("tickets", tid, revision, t.revision)
        if phases is not None and t.phase not in phases:
            raise Conflict("intent is invalid in phase " + t.phase)
        return t

    def _change(self, s, actor, t, intent, **values):
        return apply_change(s, Ticket, t.id, expected_revision=t.revision, values=values,
            event=EventSpec("ticket." + intent, actor.id, {"phase": values.get("phase", t.phase)}))

    def _event(self, s, actor, kind, entity, payload):
        append_event(s, actor.project_id, EventSpec(kind, actor.id, payload,
            entity_type=entity.__tablename__, entity_id=entity.id))

    @staticmethod
    def _repair_exhausted(t, workflow=None):
        state = t.workflow if workflow is None else workflow
        if state.get('unlimited_repairs') is True:
            return False
        return state.get("repair_cycles", 0) >= state.get("repair_limit", 3)

    def _work_blocker(self, t, dependency_pending, workflow=None):
        # Counters are authoritative; dependency checks cannot grant repair budget.
        if self._repair_exhausted(t, workflow):
            return {"reason": "needs_human", "resolution": "user must authorize a bounded repair extension"}
        if t.blocker and t.blocker.get("reason") not in ("needs_human", "dependency_revalidation"):
            return t.blocker
        if dependency_pending:
            return {"reason": "dependency_revalidation", "resolution": "required checks must pass for current scope/base"}
        return None

    def _scope(self, s, actor, t, document):
        if not isinstance(document, dict) or set(document) - {"title", "description", "uac", "dependencies", "reverts_candidate_id", "qa_profile"}:
            raise Invalid("scope fields are title/description/uac/dependencies/reverts_candidate_id/qa_profile")
        profile = document.get('qa_profile')
        if profile not in (None, 'lightweight', 'manual'):
            raise Invalid('Invalid QA profile')
        title, uac, deps = document.get("title"), document.get("uac"), document.get("dependencies", [])
        if not isinstance(title, str) or not title.strip() or not isinstance(document.get("description", ""), str):
            raise Invalid("scope title/description invalid")
        if not isinstance(uac, list) or not uac or any(not isinstance(c, dict) or
            set(c) - {"id", "text", "mode"} or not isinstance(c.get("id"), str) or not c["id"].strip() or
            not isinstance(c.get("text"), str) or not c["text"].strip() or
            c.get("mode", "automated") not in ("automated", "manual") for c in uac):
            raise Invalid("scope needs uniquely identified UAC")
        if profile == 'manual':
            uac = [{**c, 'mode': 'manual'} for c in uac]
        if len({c["id"] for c in uac}) != len(uac) or not isinstance(deps, list) or any(not isinstance(d, str) for d in deps) or len(set(deps)) != len(deps):
            raise Invalid("duplicate UAC/dependency")
        for dep in deps:
            upstream = self._row(s, Ticket, dep, actor)
            if upstream.id == t.id or upstream.phase == "cancelled":
                raise Invalid("invalid dependency")
        revert = document.get("reverts_candidate_id")
        if revert:
            candidate = self._row(s, Candidate, revert, actor)
            if candidate.status != "accepted" or not candidate.integrated_sha:
                raise Invalid("revert must reference an integrated accepted candidate")
        return {"title": title.strip(), "description": document.get("description", ""),
                "uac": uac, "dependencies": deps, "reverts_candidate_id": revert,
                **({"qa_profile": profile} if profile else {})}

    def _dag(self, s, actor, replacement=None):
        tids = set(s.scalars(select(Ticket.id).where(Ticket.project_id == actor.project_id)))
        graph = {tid: [] for tid in tids}
        for d in s.scalars(select(Dependency).where(Dependency.ticket_id.in_(tids))):
            if d.depends_on_ticket_id not in tids:
                raise Invalid("invalid/cross-project dependency")
            graph[d.ticket_id].append(d.depends_on_ticket_id)
        if replacement:
            graph[replacement[0]] = replacement[1]
        visited, active = set(), set()
        def visit(tid):
            if tid not in graph or tid in active:
                raise Invalid("dependency cycle or missing reference")
            if tid in visited:
                return
            active.add(tid)
            for up in graph[tid]:
                visit(up)
            active.remove(tid)
            visited.add(tid)
        for tid in graph:
            visit(tid)

    def _invalidate(self, s, actor, t, reason, *, keep_job_id=None):
        for job in s.scalars(select(Job).where(Job.ticket_id == t.id, Job.status.in_(ACTIVE_JOB_STATUSES))):
            if job.id == keep_job_id:
                continue  # the attempt publishing this decision finishes itself; it is not cancelled by it
            job.status = "cancelled"
            job.lease_generation += 1
            job.lease_owner = None
            job.lease_expires_at = None
            job.revision += 1
            self._event(s, actor, "job.cancellation_requested", job,
                {"reason": reason, "generation": job.lease_generation})
        for c in s.scalars(select(Candidate).where(Candidate.ticket_id == t.id,
                Candidate.status.in_(("submitted", "review_approved", "verified")))):
            c.status = "superseded"
            self._event(s, actor, "candidate.superseded", c, {"reason": reason})
        s.flush()

    def _install_scope(self, s, actor, t, doc):
        if t.phase in ("accepted", "cancelled", "integrating"):
            raise Conflict("terminal/integrating scope cannot be revised")
        doc = self._scope(s, actor, t, doc)
        self._dag(s, actor, (t.id, doc["dependencies"]))
        self._invalidate(s, actor, t, "scope_changed")
        version = (t.current_version or 0) + 1
        pending = {}
        for d in s.scalars(select(Dependency).where(Dependency.ticket_id == t.id)):
            if d.state == "needs_revalidation" and d.depends_on_ticket_id in doc["dependencies"]:
                pending[d.depends_on_ticket_id] = dict(state=d.state,
                    accepted_scope_version=d.accepted_scope_version, accepted_candidate_id=d.accepted_candidate_id,
                    integration_sha=d.integration_sha, revalidation={**(d.revalidation or {}),
                        "request_id": new_id(), "scope_version": version})
            s.delete(d)
        s.flush()
        s.add(TicketVersion(ticket_id=t.id, version=version, title=doc["title"],
            description=doc["description"], uac=doc["uac"], scope={"dependencies": doc["dependencies"],
                "reverts_candidate_id": doc["reverts_candidate_id"],
                **({"qa_profile": doc["qa_profile"]} if doc.get("qa_profile") else {})},
            content_digest=sha256_bytes(canonical_json(doc)), created_by=actor.id))
        s.flush()
        for dep in doc["dependencies"]:
            d = Dependency(ticket_id=t.id, depends_on_ticket_id=dep, **pending.get(dep, {}))
            s.add(d)
            s.flush()
            if dep in pending:
                self._dep_change(s, actor, d, scope_version=version, state="needs_revalidation")
        s.flush()
        return self._change(s, actor, t, "scope_revised", title=doc["title"], current_version=version,
            phase="scope_review", blocker={"reason": "dependency_revalidation",
                "resolution": "required checks must pass for current scope/base"} if pending else None,
            workflow={"repair_cycles": 0, "attempts": {},
                **({'creation_key': t.workflow['creation_key']} if 'creation_key' in t.workflow else {})})

    def create_ticket(self, actor, document, *, idempotency_key=None):
        """Create a ticket proposal. With an idempotency key a retried command returns the ticket the first
        call created (the key is stored on the ticket in the same transaction), never a duplicate."""
        with self.db.write() as s:
            self._permit(s, actor, "user", "po")
            if not isinstance(document, dict):
                raise Invalid("scope document must be an object")
            if idempotency_key is not None:
                if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                    raise Invalid("idempotency key must be a non-empty string")
                for known in s.scalars(select(Ticket).where(Ticket.project_id == actor.project_id)):
                    if known.workflow.get("creation_key") == idempotency_key:
                        return known
            if actor.role == 'po' or document.get('qa_profile') is None:
                document = {**document, 'qa_profile': s.get(Project, actor.project_id).workflow.get('qa_profile', 'lightweight')}
            # A new PO proposal starts unapproved; existing scope can only change with user confirmation.
            number = max(s.scalars(select(Ticket.number).where(Ticket.project_id == actor.project_id)), default=0) + 1
            t = Ticket(project_id=actor.project_id, number=number, title="Untitled")
            s.add(t)
            s.flush()
            t = self._install_scope(s, actor, t, document)
            if idempotency_key is not None:
                t = self._change(s, actor, t, "creation_recorded",
                                 workflow={**t.workflow, "creation_key": idempotency_key})
            return t

    def edit_scope(self, actor, ticket_id, expected_revision, document):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision)
            return self._install_scope(s, actor, t, document)

    def propose_scope(self, actor, ticket_id, expected_revision, document, *, idempotency_key=None):
        """PO revision proposal. A retried call with the same key returns the stored proposal unchanged."""
        with self.db.write() as s:
            self._permit(s, actor, "po")
            t = self._ticket(s, actor, ticket_id, expected_revision,
                ("scope_review", "ready", "development", "technical_review", "qa", "uat"))
            job = s.get(Job, actor.job_id)
            if job.ticket_id != t.id or job.scope_version != t.current_version:
                raise Conflict("proposal belongs to stale scope")
            doc = self._scope(s, actor, t, document)
            self._dag(s, actor, (t.id, doc["dependencies"]))
            message, created = append_message(s, project_id=t.project_id, ticket_id=t.id,
                thread_id="scope:" + t.id, sender=actor.id, recipient="user", body="Scope revision proposal",
                idempotency_key=idempotency_key,
                meta={"intent": "scope_proposal", "base_version": t.current_version, "document": doc})
            if created:
                self._change(s, actor, t, "scope_proposed")
            return message.id

    def decide_proposal(self, actor, ticket_id, expected_revision, proposal_id, accept):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision,
                ("scope_review", "ready", "development", "technical_review", "qa", "uat"))
            proposal = self._row(s, Message, proposal_id, actor)
            if proposal.ticket_id != t.id or proposal.meta.get("intent") != "scope_proposal" or proposal.meta["base_version"] != t.current_version:
                raise Conflict("proposal identity/scope changed")
            if s.scalar(select(Message.id).where(Message.reply_to == proposal_id)):
                raise Conflict("proposal already decided")
            if type(accept) is not bool:
                raise Invalid("explicit accept/reject decision required")
            append_message(s, project_id=t.project_id, ticket_id=t.id, thread_id=proposal.thread_id,
                sender=actor.id, kind="system", reply_to=proposal_id, body="accepted" if accept else "rejected",
                meta={"intent": "scope_decision"})
            return self._install_scope(s, actor, t, proposal.meta["document"]) if accept else self._change(s, actor, t, "proposal_rejected")

    def approve_scope(self, actor, items: list[ApprovalItem]):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            if not items or len({i.ticket_id for i in items}) != len(items):
                raise Invalid("nonempty unique approval batch required")
            tickets = [self._ticket(s, actor, i.ticket_id, i.expected_revision, ("scope_review",)) for i in items]
            self._dag(s, actor)
            for t, i in zip(tickets, items):
                if t.current_version != i.scope_version:
                    raise Conflict("approval scope version changed")
                for d in s.scalars(select(Dependency).where(Dependency.ticket_id == t.id)):
                    if self._row(s, Ticket, d.depends_on_ticket_id, actor).phase == "cancelled":
                        raise Invalid("dependency is cancelled")
            batch = new_id()
            for t in tickets:
                s.add(Approval(project_id=t.project_id, type="scope", user_id=actor.id,
                    ticket_id=t.id, scope_version=t.current_version, batch_id=batch))
                s.flush()
                self._refresh_dependencies(s, actor, t)
                pending = s.scalar(select(Dependency.id).where(Dependency.ticket_id == t.id,
                    Dependency.state == "needs_revalidation"))
                self._change(s, actor, t, "scope_approved", phase="ready",
                    blocker=self._work_blocker(t, bool(pending)))
            return batch

    def _dep_change(self, s, actor, d, *, scope_version=None, **values):
        for k, value in values.items():
            setattr(d, k, value)
        s.flush()
        self._event(s, actor, "dependency.changed", d, {"state": d.state,
            "ticket_id": d.ticket_id, "upstream_ticket_id": d.depends_on_ticket_id,
            "scope_version": s.get(Ticket, d.ticket_id).current_version if scope_version is None else scope_version,
            "accepted_scope_version": d.accepted_scope_version, "accepted_candidate_id": d.accepted_candidate_id,
            "integration_sha": d.integration_sha, "revalidation": d.revalidation})

    def _refresh_dependencies(self, s, actor, t):
        for d in s.scalars(select(Dependency).where(Dependency.ticket_id == t.id)):
            up = self._row(s, Ticket, d.depends_on_ticket_id, actor)
            cid = up.workflow.get("accepted_candidate_id")
            c = s.get(Candidate, cid) if cid else None
            if d.state == "waiting" and up.phase == "accepted" and c and c.ticket_id == up.id and c.project_id == t.project_id and c.status == "accepted" and c.integrated_sha:
                stale_contract = up.workflow.get("contract_change") or s.scalar(select(Dependency.id).where(
                    Dependency.ticket_id == up.id, Dependency.state == "needs_revalidation"))
                self._dep_change(s, actor, d, state="needs_revalidation" if stale_contract else "satisfied",
                    accepted_scope_version=c.scope_version, accepted_candidate_id=c.id, integration_sha=c.integrated_sha,
                    revalidation={"request_id": new_id(), "trigger_ticket_id": up.id, "reported_by": actor.id,
                        "reason": "accepted upstream has contract changes requiring checks"} if stale_contract else None)

    def _eligible(self, s, t):
        approved = s.scalar(select(Approval.id).where(Approval.type == "scope", Approval.ticket_id == t.id,
            Approval.scope_version == t.current_version))
        if t.phase not in ("ready", "development") or t.blocker or self._repair_exhausted(t) or not approved:
            return False
        for d in s.scalars(select(Dependency).where(Dependency.ticket_id == t.id)):
            c = s.get(Candidate, d.accepted_candidate_id) if d.accepted_candidate_id else None
            if d.state != "satisfied" or not c or c.ticket_id != d.depends_on_ticket_id or c.project_id != t.project_id or c.status != "accepted" or c.scope_version != d.accepted_scope_version or c.integrated_sha != d.integration_sha:
                return False
        return True

    def startable(self, s, job) -> bool:
        """Read-only scheduler pre-check run inside the caller's claim transaction.

        Mirrors bind_attempt (which stays authoritative after the claim): the ticket must be at
        the job's scope version, unblocked, in the phase that this stage/role works on, and for
        development fully eligible (approved scope, satisfied dependencies, repair budget).
        """
        t = s.get(Ticket, job.ticket_id)
        if t is None or t.project_id != job.project_id or t.current_version != job.scope_version:
            return False
        if t.phase not in ("ready", "development", "technical_review", "qa") or t.blocker or self._repair_exhausted(t):
            return False
        stage = "development" if t.phase == "ready" else t.phase
        roles = {"development": "developer", "technical_review": "technical-lead", "qa": "qa"}
        if job.stage != stage or (job.runtime_ref or {}).get("role") != roles[stage]:
            return False
        return stage != "development" or self._eligible(s, t)

    def eligible(self, actor, ticket_id):
        with self.db.read() as s:
            self._permit(s, actor, "user", "scheduler")
            return self._eligible(s, self._row(s, Ticket, ticket_id, actor))

    def _attempt(self, s, actor, t, attempt, stage, *, bound=True):
        job = self._row(s, Job, attempt.job_id, actor)
        if (job.ticket_id != t.id or job.scope_version != t.current_version or attempt.scope_version != t.current_version or
            job.lease_generation != attempt.generation or job.status != "running" or
            not job.lease_expires_at or job.lease_expires_at <= utcnow() or job.stage != stage):
            raise Conflict("stale attempt/lease/scope/stage")
        if actor.role in ("developer", "technical-lead", "qa") and (actor.job_id != job.id or actor.generation != attempt.generation):
            raise Forbidden("agent cannot borrow another attempt")
        stored = t.workflow.get("attempts", {}).get(stage)
        if bound and stored != {"job_id": job.id, "generation": attempt.generation, "scope_version": attempt.scope_version}:
            raise Conflict("attempt was replaced")
        return job

    def bind_attempt(self, actor, ticket_id, expected_revision, attempt: Attempt):
        with self.db.write() as s:
            self._permit(s, actor, "scheduler")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("ready", "development", "technical_review", "qa"))
            if t.blocker or self._repair_exhausted(t):
                raise Conflict("ticket is blocked")
            stage = "development" if t.phase == "ready" else t.phase
            if stage == "development" and not self._eligible(s, t):
                raise Forbidden("scope/dependencies not eligible")
            job = self._attempt(s, actor, t, attempt, stage, bound=False)
            expected_role = {"development": "developer", "technical_review": "technical-lead", "qa": "qa"}[stage]
            if (job.runtime_ref or {}).get("role") != expected_role:
                raise Invalid("stage role mismatch")
            workflow = dict(t.workflow)
            attempts = dict(workflow.get("attempts", {}))
            old = attempts.get(stage)
            if old and old["job_id"] != job.id:
                previous = s.get(Job, old["job_id"])
                if previous and previous.status in ACTIVE_JOB_STATUSES:
                    raise Conflict("reconcile old attempt before replacement")
            attempts[stage] = {"job_id": job.id, "generation": attempt.generation, "scope_version": attempt.scope_version}
            workflow["attempts"] = attempts
            return self._change(s, actor, t, "attempt_bound", phase=stage, workflow=workflow)

    def initialize_base(self, actor, expected_revision, base_sha):
        with self.db.write() as s:
            self._permit(s, actor, "integrator")
            p = self._row(s, Project, actor.project_id, actor)
            if p.workflow.get("accepted_tip"):
                raise Conflict("base is already initialized")
            evidence.digest(base_sha, (40, 64))
            return apply_change(s, Project, p.id, expected_revision=expected_revision,
                values={"workflow": {**p.workflow, "accepted_tip": base_sha}},
                event=EventSpec("project.base_initialized", actor.id, {"accepted_tip": base_sha}))

    def submit_candidate(self, actor, ticket_id, expected_revision, attempt, *, commit_artifact_id,
                         commit_receipt_id, base_sha, submission_key):
        with self.db.write() as s:
            self._permit(s, actor, "developer")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("development",))
            self._attempt(s, actor, t, attempt, "development")
            if not self._eligible(s, t):
                raise Forbidden("scope/dependencies no longer eligible")
            p = s.get(Project, t.project_id)
            if base_sha != p.workflow.get("accepted_tip"):
                raise Conflict("base changed; reconcile implementation")
            commit = evidence.artifact(s, self.store, t.project_id, commit_artifact_id, "git_commit")
            if commit.storage != "git" or not submission_key:
                raise Invalid("managed commit and submission key required")
            # Git artifacts deduplicate by SHA; provenance belongs to a separate
            # immutable broker receipt per attempt, never to mutable SHA metadata.
            receipt = evidence.artifact(s, self.store, t.project_id, commit_receipt_id, "report")
            source = {"job_id": attempt.job_id, "generation": attempt.generation, "scope_version": attempt.scope_version}
            expected = {"kind": "candidate_commit", "project_id": t.project_id, "ticket_id": t.id,
                "commit_artifact_id": commit.id, "commit_sha": commit.checksum, "base_sha": base_sha,
                "source_attempt": source}
            if receipt.meta.get("producer") != "broker" or evidence.document(s, self.store, receipt.id) != expected:
                raise Conflict("commit receipt belongs to another attempt/scope/base/commit")
            if s.scalar(select(Candidate.id).where(Candidate.project_id == t.project_id, Candidate.idempotency_key == submission_key)):
                raise Conflict("submission key already used")
            c = Candidate(project_id=t.project_id, ticket_id=t.id, scope_version=t.current_version,
                job_id=attempt.job_id, idempotency_key=submission_key, commit_sha=commit.checksum,
                base_sha=base_sha, commit_artifact_id=commit.id,
                integration={"source_attempt": source, "commit_receipt_id": receipt.id})
            s.add(c)
            s.flush()
            append_message(s, project_id=t.project_id, ticket_id=t.id, thread_id="candidate:" + t.id,
                sender=actor.id, kind="system", body="Candidate commit submitted",
                attachment_ids=[receipt.id], meta={"intent": "candidate_submitted", "candidate_id": c.id,
                    "source_attempt": source, "commit_artifact_id": commit.id})
            workflow = {**t.workflow, "candidate_id": c.id}
            self._change(s, actor, t, "candidate_submitted", phase="technical_review", workflow=workflow)
            return c

    def _candidate(self, s, actor, t, cid):
        c = self._row(s, Candidate, cid, actor)
        if c.ticket_id != t.id or c.scope_version != t.current_version or t.workflow.get("candidate_id") != c.id or c.status in ("superseded", "rejected"):
            raise Conflict("candidate no longer current")
        return c

    def attach_target(self, actor, ticket_id, expected_revision, candidate_id, *, build_artifact_id, target_artifact_id, target_digest):
        with self.db.write() as s:
            self._permit(s, actor, "builder")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("technical_review", "qa"))
            c = self._candidate(s, actor, t, candidate_id)
            if c.status not in ("submitted", "review_approved"):
                raise Conflict("leave verified target before rebuilding")
            source = (c.integration or {}).get("source_attempt")
            job = s.get(Job, c.job_id)
            target_row = evidence.artifact(s, self.store, t.project_id, target_artifact_id, "target_manifest")
            if (not source or not job or job.lease_generation != source["generation"] or
                job.status not in ("running", "succeeded") or
                target_row.meta.get("producer") != "builder" or target_row.meta.get("source_attempt") != source):
                raise Conflict("build belongs to a stale source attempt")
            c.build_artifact_id = build_artifact_id
            c.target_artifact_id, c.target_digest = target_artifact_id, target_digest
            evidence.target(s, self.store, c, target_artifact_id, target_digest)
            s.flush()
            return self._change(s, actor, t, "target_attached")

    def approve_review(self, actor, ticket_id, expected_revision, attempt, candidate_id):
        with self.db.write() as s:
            self._permit(s, actor, "technical-lead")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("technical_review",))
            self._attempt(s, actor, t, attempt, "technical_review")
            c = self._candidate(s, actor, t, candidate_id)
            evidence.target(s, self.store, c, c.target_artifact_id, c.target_digest)
            c.status = "review_approved"
            s.flush()
            return self._change(s, actor, t, "review_approved", phase="qa")

    def open_uat(self, actor, ticket_id, expected_revision, attempt, candidate_id, verification_id, smoke_artifact_id):
        with self.db.write() as s:
            self._permit(s, actor, "verification")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("qa",))
            self._attempt(s, actor, t, attempt, "qa")
            c = self._candidate(s, actor, t, candidate_id)
            v = evidence.verification(s, self.store, c, verification_id)
            if v.results.get("job_id") != attempt.job_id or v.results.get("generation") != attempt.generation:
                raise Conflict("verification was published by a stale QA attempt")
            smoke = evidence.artifact(s, self.store, t.project_id, smoke_artifact_id, "report")
            if smoke.meta.get("producer") != "verification":
                raise Invalid("smoke receipt must be published by verification service")
            receipt = evidence.document(s, self.store, smoke.id)
            if receipt != {"target_artifact_id": c.target_artifact_id, "target_digest": c.target_digest,
                           "status": "passed", "kind": "preview_smoke"}:
                raise Invalid("preview smoke does not match tested target")
            c.status = "verified"
            c.evidence_artifact_ids = list(dict.fromkeys([*v.evidence_artifact_ids, smoke.id]))
            c.preview = {"smoke_artifact_id": smoke.id, "verification_id": v.id}
            s.flush()
            return self._change(s, actor, t, "uat_opened", phase="uat")

    def waive_uncertain_qa(self, actor, ticket_id, expected_revision, *, candidate_id, verification_id,
                          target_artifact_id, target_digest, diagnosis_artifact_id, evidence_ids,
                          manual_uac_ids, reason):
        from .qa_resolution import qualify
        from app.persistence.models import QaWaiver
        with self.db.write() as s:
            self._permit(s, actor, 'user')
            t = self._ticket(s, actor, ticket_id, expected_revision, ('qa',))
            c = self._candidate(s, actor, t, candidate_id)
            if c.status != 'review_approved' or (target_artifact_id, target_digest) != (c.target_artifact_id, c.target_digest):
                raise Conflict('QA candidate/target changed')
            if s.get(Project, t.project_id).workflow.get('accepted_tip') != c.base_sha:
                raise Conflict('Accepted base changed; fresh candidate/QA/UAT required')
            if t.blocker and t.blocker.get('reason') != 'needs_human':
                raise Conflict('Resolve the ticket blocker before a manual QA decision')
            try:
                qualified = qualify(s, self.store, c, verification_id, diagnosis_artifact_id)
            except (ValueError, KeyError, TypeError) as exc:
                raise Invalid('QA decision evidence is malformed or unavailable') from exc
            if (len(evidence_ids) != len(set(evidence_ids)) or set(evidence_ids) != set(qualified['evidence_ids']) or
                    len(manual_uac_ids) != len(set(manual_uac_ids)) or sorted(manual_uac_ids) != qualified['manual_uac_ids']):
                raise Conflict('Manual QA ownership or displayed evidence differs from current diagnosis')
            if not isinstance(reason, str) or not reason.strip():
                raise Invalid('Manual QA ownership requires a reason')
            decision = QaWaiver(project_id=t.project_id, ticket_id=t.id, scope_version=t.current_version,
                candidate_id=c.id, verification_id=verification_id, target_artifact_id=c.target_artifact_id,
                target_digest=c.target_digest, diagnosis_artifact_id=diagnosis_artifact_id,
                user_id=actor.id, reason=reason.strip(), manual_uac_ids=qualified['manual_uac_ids'],
                excluded_test_ids=qualified['excluded_test_ids'], evidence_artifact_ids=qualified['evidence_ids'])
            s.add(decision)
            s.flush()
            # The authoritative runner already proved smoke on exactly these build bytes.
            smoke = self.store.put_json(s, project_id=t.project_id, kind='report', name='preview-smoke.json',
                document={'target_artifact_id': c.target_artifact_id, 'target_digest': c.target_digest,
                          'status': 'passed', 'kind': 'preview_smoke'},
                meta={'producer': 'verification', 'source_verification_id': verification_id,
                      'qa_waiver_id': decision.id})
            c.evidence_artifact_ids = [*decision.evidence_artifact_ids, smoke.id]
            c.preview = {'verification_id': verification_id, 'smoke_artifact_id': smoke.id, 'qa_waiver_id': decision.id}
            c.status = 'verified'
            s.flush()
            self._event(s, actor, 'qa.manual_ownership_accepted', decision,
                        {'target_digest': c.target_digest, 'verification_id': verification_id,
                         'manual_uac_ids': decision.manual_uac_ids})
            return self._change(s, actor, t, 'manual_uat_opened', phase='uat', blocker=None)

    def reopen_qa(self, actor, ticket_id, expected_revision, candidate_id, verification_id, reason):
        """Withdraw an unaccepted QA result for trusted evidence correction, preserving reviewed code.

        No agent tool exposes this command. Accepted/integrating candidates and
        user approvals cannot be invalidated through this recovery path.
        """
        with self.db.write() as s:
            self._permit(s, actor, 'verification')
            t = self._ticket(s, actor, ticket_id, expected_revision, ('uat',))
            c = self._candidate(s, actor, t, candidate_id)
            evidence.verification(s, self.store, c, verification_id)
            if c.status != 'verified' or c.preview.get('verification_id') != verification_id:
                raise Conflict('QA correction belongs to an obsolete verification')
            if not isinstance(reason, str) or not reason.strip():
                raise Invalid('QA evidence correction requires a reason')
            for job in s.scalars(select(Job).where(Job.ticket_id == t.id, Job.stage == 'qa',
                                                   Job.status.in_(ACTIVE_JOB_STATUSES))):
                job.status, job.lease_owner, job.lease_expires_at = 'cancelled', None, None
                job.lease_generation += 1
                self._event(s, actor, 'job.cancellation_requested', job,
                    {'reason': 'qa_evidence_correction', 'generation': job.lease_generation})
            c.status, c.preview = 'review_approved', {}
            s.flush()
            attempts = {key: value for key, value in t.workflow.get('attempts', {}).items() if key != 'qa'}
            return self._change(s, actor, t, 'qa_reopened', phase='qa',
                workflow={**t.workflow, 'attempts': attempts, 'qa_correction': {
                    'verification_id': verification_id, 'reason': reason.strip()[:2000]}})

    def accept_uat(self, actor, ticket_id, expected_revision, candidate_id, scope_version,
                   target_artifact_id, target_digest, verification_id, evidence_ids, manual_uac_ids=()):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("uat",))
            c = self._candidate(s, actor, t, candidate_id)
            if scope_version != t.current_version or c.target_artifact_id != target_artifact_id or c.target_digest != target_digest:
                raise Conflict("UAT scope/target changed")
            v = evidence.verification(s, self.store, c, verification_id)
            if (c.preview or {}).get("verification_id") != v.id or set(evidence_ids) != set(c.evidence_artifact_ids) or len(evidence_ids) != len(set(evidence_ids)):
                raise Conflict("UAT evidence differs from displayed evidence")
            for aid in evidence_ids:
                evidence.artifact(s, self.store, t.project_id, aid)
            scope = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == t.id, TicketVersion.version == t.current_version))
            required_manual = {u["id"] for u in scope.uac if u.get("mode") == "manual"}
            from .qa_resolution import waiver_for
            qa_decision = waiver_for(s, c, v.id)
            if qa_decision:
                required_manual.update(qa_decision.manual_uac_ids)
            if len(manual_uac_ids) != len(set(manual_uac_ids)) or set(manual_uac_ids) != required_manual:
                raise Invalid("manual UAC confirmation incomplete")
            p = s.get(Project, t.project_id)
            if p.workflow.get("accepted_tip") != c.base_sha:
                raise Conflict("accepted base changed; new candidate/QA/UAT required")
            approval = Approval(project_id=t.project_id, type="uat", user_id=actor.id, ticket_id=t.id,
                scope_version=t.current_version, candidate_id=c.id, target_artifact_id=c.target_artifact_id,
                target_digest=c.target_digest, evidence_artifact_ids=list(evidence_ids), details={"verification_id": v.id,
                    "manual_uac_ids": sorted(required_manual)})
            s.add(approval)
            s.flush()
            c.integration = {"operation_id": new_id(), "status": "pending", "expected_base": c.base_sha,
                "target_sha": c.commit_sha, "target_artifact_id": c.target_artifact_id, "approval_id": approval.id}
            s.flush()
            self._change(s, actor, t, "uat_accepted", phase="integrating")
            return c.integration

    def finish_integration(self, actor, ticket_id, expected_revision, candidate_id, operation_id, observed_tip):
        """Trusted integrator receipt only. Does not mutate Git; DEV-012 supplies/reconciles it."""
        with self.db.write() as s:
            self._permit(s, actor, "integrator")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("integrating",))
            c = self._candidate(s, actor, t, candidate_id)
            op = dict(c.integration or {})
            p = s.get(Project, t.project_id)
            if op.get("operation_id") != operation_id or op.get("status") != "pending" or observed_tip != op.get("target_sha") or p.workflow.get("accepted_tip") != op.get("expected_base"):
                raise Conflict("integration must reconcile exact operation/base/Git receipt")
            c.integrated_sha, c.status = observed_tip, "accepted"
            c.integration = {**op, "status": "done"}
            s.flush()
            history = [*p.workflow.get("tip_history", []), p.workflow.get("accepted_tip")][-2000:]
            apply_change(s, Project, p.id, expected_revision=p.revision,
                values={"workflow": {**p.workflow, "accepted_tip": observed_tip, "tip_history": history}},
                event=EventSpec("project.accepted_tip_changed", actor.id, {"accepted_tip": observed_tip}))
            t = self._change(s, actor, t, "integrated", phase="accepted",
                workflow={**t.workflow, "accepted_candidate_id": c.id})
            # The accepted base moved: work built on the old base cannot carry its review/QA/UAT forward.
            self._base_advanced(s, actor, t.project_id, observed_tip, exclude=t.id)
            version = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == t.id,
                                                           TicketVersion.version == t.current_version))
            reverted = (version.scope or {}).get("reverts_candidate_id") if version else None
            if reverted:  # a revert changes the upstream contract: downstream must revalidate
                rc = s.get(Candidate, reverted)
                up = s.get(Ticket, rc.ticket_id) if rc else None
                if up is not None and up.phase == "accepted":
                    self._contract_change(s, actor, up, f"accepted candidate reverted by ticket #{t.number}",
                                          allow_integrating=True)
            for downstream in s.scalars(select(Ticket).where(Ticket.project_id == t.project_id)):
                self._refresh_dependencies(s, actor, downstream)
            return c

    def _base_advanced(self, s, actor, project_id, tip, *, exclude=None):
        """Candidates in review/QA/UAT built on an older base go back to development for a rebase.

        Their earlier review/QA/UAT stay history; the rebased candidate needs its own. Not a repair cycle."""
        for other in list(s.scalars(select(Ticket).where(Ticket.project_id == project_id,
                Ticket.phase.in_(("technical_review", "qa", "uat"))))):
            cid = other.workflow.get("candidate_id")
            c = s.get(Candidate, cid) if cid else None
            if other.id == exclude or c is None or c.base_sha == tip:
                continue
            self._invalidate(s, actor, other, "base_changed")
            append_message(s, project_id=project_id, ticket_id=other.id, thread_id="ticket:" + other.id,
                sender=actor.id, kind="system",
                body="Accepted base changed; the candidate must be rebased and pass technical review, QA and UAT again.",
                meta={"intent": "rebase_request", "scope_version": other.current_version, "candidate_id": c.id,
                      "old_base": c.base_sha, "new_base": tip})
            self._change(s, actor, other, "base_changed", phase="development",
                         workflow={**other.workflow, "candidate_id": None, "attempts": {}})

    def integration_plan(self, actor, ticket_id):
        """Re-validate an accepted UAT before Git is touched: exact approval, target, evidence and base."""
        with self.db.read() as s:
            self._permit(s, actor, "integrator")
            t = self._row(s, Ticket, ticket_id, actor)
            if t.phase != "integrating":
                raise Conflict("ticket is not integrating")
            c = self._candidate(s, actor, t, t.workflow.get("candidate_id"))
            op = dict(c.integration or {})
            if op.get("status") != "pending":
                raise Conflict("no pending integration operation")
            approval = s.get(Approval, op.get("approval_id"))
            if (approval is None or approval.type != "uat" or approval.ticket_id != t.id or approval.candidate_id != c.id
                    or approval.scope_version != t.current_version or approval.target_artifact_id != c.target_artifact_id
                    or approval.target_digest != c.target_digest
                    or set(approval.evidence_artifact_ids) != set(c.evidence_artifact_ids)):
                raise Conflict("UAT approval does not pin the candidate/target/evidence being integrated")
            evidence.verification(s, self.store, c, (approval.details or {}).get("verification_id"))
            for aid in approval.evidence_artifact_ids:
                evidence.artifact(s, self.store, t.project_id, aid)
            if (op.get("target_sha") != c.commit_sha or op.get("expected_base") != c.base_sha
                    or op.get("target_artifact_id") != c.target_artifact_id):
                raise Conflict("integration operation does not match the approved candidate")
            return {"ticket_id": t.id, "revision": t.revision, "candidate_id": c.id, "operation_id": op["operation_id"],
                    "expected_base": op["expected_base"], "target_sha": op["target_sha"], "approval_id": approval.id,
                    "db_tip": s.get(Project, t.project_id).workflow.get("accepted_tip")}

    def integration_blocked(self, actor, ticket_id, expected_revision, candidate_id, operation_id, *, reason,
                            observed_tip=None, evidence_artifact_id=None):
        """Git/DB disagree in a way no automatic step may resolve: keep the evidence, block, never reset."""
        with self.db.write() as s:
            self._permit(s, actor, "integrator")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("integrating",))
            c = self._candidate(s, actor, t, candidate_id)
            op = dict(c.integration or {})
            if op.get("operation_id") != operation_id or op.get("status") != "pending":
                raise Conflict("not the pending integration operation")
            if evidence_artifact_id:
                evidence.artifact(s, self.store, t.project_id, evidence_artifact_id, "report")
            c.integration = {**op, "status": "blocked", "reason": str(reason)[:500], "observed_tip": observed_tip,
                             "evidence_artifact_id": evidence_artifact_id}
            s.flush()
            return self._change(s, actor, t, "integration_blocked", blocker={"reason": "integration_blocked",
                "resolution": "operator must inspect the Git/DB evidence; nothing was reset automatically",
                "detail": str(reason)[:500], "evidence_artifact_id": evidence_artifact_id})

    def integration_diverged(self, actor, ticket_id, expected_revision, candidate_id, operation_id, observed_tip):
        with self.db.write() as s:
            self._permit(s, actor, "integrator")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("integrating",))
            c = self._candidate(s, actor, t, candidate_id)
            op = dict(c.integration or {})
            if op.get("operation_id") != operation_id or op.get("status") != "pending" or observed_tip in (op.get("target_sha"), op.get("expected_base")):
                raise Conflict("not a reconciled divergent operation")
            evidence.digest(observed_tip, (40, 64))
            p = s.get(Project, t.project_id)
            # Only a tip the product itself finalised may be adopted. An unknown ref is integration_blocked.
            if p.workflow.get("accepted_tip") != observed_tip:
                raise Conflict("observed tip is not the accepted tip recorded by a finished integration")
            c.integration = {**op, "status": "diverged", "observed_tip": observed_tip}
            s.flush()
            self._invalidate(s, actor, t, "integration_base_changed")
            append_message(s, project_id=t.project_id, ticket_id=t.id, thread_id="ticket:" + t.id,
                sender=actor.id, kind="system",
                body="Another integration moved the accepted base first; rebase, then review, QA and UAT again.",
                meta={"intent": "rebase_request", "scope_version": t.current_version, "candidate_id": c.id,
                      "old_base": c.base_sha, "new_base": observed_tip})
            return self._change(s, actor, t, "integration_diverged", phase="development",
                workflow={**t.workflow, "candidate_id": None, "attempts": {}},
                blocker=None)

    def request_changes(self, actor, ticket_id, expected_revision, candidate_id, reason, attempt=None):
        with self.db.write() as s:
            self._permit(s, actor, "user", "technical-lead", "qa", "verification")
            permitted = {"user": "uat", "technical-lead": "technical_review", "qa": "qa", "verification": "qa"}
            t = self._ticket(s, actor, ticket_id, expected_revision, (permitted[actor.role],))
            self._candidate(s, actor, t, candidate_id)
            if actor.role != "user":
                if attempt is None:
                    raise Invalid("attempt required")
                self._attempt(s, actor, t, attempt, t.phase)
            if not isinstance(reason, str) or not reason.strip():
                raise Invalid("feedback reason required")
            cycles = t.workflow.get("repair_cycles", 0) + 1
            # Every rejection path, including user UAT, identifies the exact
            # immutable candidate the next developer attempt must repair.
            origin = s.get(Job, attempt.job_id) if attempt is not None else None
            append_message(s, project_id=t.project_id, ticket_id=t.id, thread_id="ticket:" + t.id,
                sender=actor.id if actor.role == "user" else "agent:" + actor.role,
                recipient="role:developer", body=reason[:7900],
                idempotency_key=f"repair:{t.id}:v{t.current_version}:r{t.revision}:{candidate_id}",
                meta={"intent": "repair_feedback", "candidate_id": candidate_id,
                      "scope_version": t.current_version, "repair_cycle": cycles,
                      "job_id": attempt.job_id if attempt else None,
                      "generation": attempt.generation if attempt else None,
                      "fake": bool((origin.runtime_ref or {}).get("fake")) if origin else False})
            self._invalidate(s, actor, t, "request_changes", keep_job_id=attempt.job_id if actor.role != "user" else None)
            return self._change(s, actor, t, "changes_requested", phase="development",
                workflow={**t.workflow, "repair_cycles": cycles, "candidate_id": None, "attempts": {}, "feedback": reason},
                blocker={"reason": "needs_human", "resolution": "user must authorize a bounded repair extension"}
                    if self._repair_exhausted(t, {**t.workflow, 'repair_cycles': cycles}) else None)

    def authorize_repair(self, actor, ticket_id, expected_revision, additional_cycles=1):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("development",))
            if not self._repair_exhausted(t) or type(additional_cycles) is not int or not 1 <= additional_cycles <= 3:
                raise Invalid("bounded repair extension requires needs_human")
            workflow = {**t.workflow, "repair_limit": t.workflow.get("repair_limit", 3) + additional_cycles}
            remaining = s.scalar(select(Dependency.id).where(Dependency.ticket_id == t.id, Dependency.state != "satisfied"))
            blocker = self._work_blocker(t, bool(remaining), workflow)
            return self._change(s, actor, t, "repair_authorized", blocker=blocker, workflow=workflow)

    def cancel(self, actor, ticket_id, expected_revision):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision,
                ("draft", "scope_review", "ready", "development", "technical_review", "qa", "uat"))
            self._invalidate(s, actor, t, "ticket_cancelled")
            return self._change(s, actor, t, "cancelled", phase="cancelled", blocker=None)

    def contract_changed(self, actor, upstream_id, expected_revision, reason):
        with self.db.write() as s:
            self._permit(s, actor, "integrator")
            up = self._ticket(s, actor, upstream_id, expected_revision, ("accepted",))
            if not isinstance(reason, str) or not reason.strip():
                raise Invalid("contract change reason required")
            return self._contract_change(s, actor, up, reason)

    def _contract_change(self, s, actor, up, reason, *, allow_integrating=False):
        change = {"id": new_id(), "reason": reason, "reported_by": actor.id,
            "base_sha": s.get(Project, up.project_id).workflow.get("accepted_tip")}
        pending, visited, edges = [up.id], {up.id}, {}
        while pending:
            tid = pending.pop()
            for d in s.scalars(select(Dependency).where(Dependency.depends_on_ticket_id == tid)):
                edges[d.id] = d
                if d.ticket_id not in visited:
                    visited.add(d.ticket_id)
                    pending.append(d.ticket_id)
        for tid in sorted(visited - {up.id}):
            down = self._row(s, Ticket, tid, actor)
            if down.phase == "integrating" and not allow_integrating:
                raise Conflict("reconcile downstream integration first")
            for d in edges.values():
                if d.ticket_id != tid:
                    continue
                self._dep_change(s, actor, d, state="needs_revalidation",
                    revalidation={"reason": reason, "trigger_ticket_id": up.id, "reported_by": actor.id,
                        "contract_change_id": change["id"], "request_id": new_id()})
            if down.phase == "integrating":
                continue  # its pending operation is built on the old base and will diverge when reconciled
            if down.phase in ("accepted", "cancelled"):
                # Acceptance is historical. Code changes need a new ticket,
                # not a blocker that no command can resolve on this ticket.
                self._change(s, actor, down, "dependency_followup_required")
                continue
            self._invalidate(s, actor, down, "dependency_contract_changed")
            self._change(s, actor, down, "dependency_invalidated",
                phase="development" if down.phase in ("development", "technical_review", "qa", "uat") else down.phase,
                workflow={**down.workflow, "candidate_id": None, "attempts": {}},
                blocker=self._work_blocker(down, True))
        return self._change(s, actor, up, "contract_change_recorded",
            workflow={**up.workflow, "contract_change": change})

    def revalidate_dependency(self, actor, ticket_id, expected_revision, upstream_id, report_artifact_id):
        with self.db.write() as s:
            self._permit(s, actor, "verification")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("ready", "development", "technical_review", "qa", "uat"))
            d = s.scalar(select(Dependency).where(Dependency.ticket_id == t.id, Dependency.depends_on_ticket_id == upstream_id))
            if not d or d.state != "needs_revalidation":
                raise Invalid("dependency is not pending revalidation")
            up = self._row(s, Ticket, upstream_id, actor)
            c = self._row(s, Candidate, up.workflow.get("accepted_candidate_id"), actor)
            if up.phase != "accepted" or c.ticket_id != up.id or c.status != "accepted" or not c.integrated_sha:
                raise Conflict("upstream is not integrated and accepted")
            report = evidence.artifact(s, self.store, t.project_id, report_artifact_id, "report")
            if report.meta.get("producer") != "verification":
                raise Invalid("revalidation report must be published by verification service")
            receipt = evidence.document(s, self.store, report.id)
            tip = s.get(Project, t.project_id).workflow.get("accepted_tip")
            if (receipt.get("status") != "passed" or receipt.get("fake_provider") is not False or receipt.get("uac_changed") is not False or
                receipt.get("infrastructure_failure") is not False or receipt.get("revalidation_id") != d.revalidation.get("request_id") or
                receipt.get("ticket_id") != t.id or receipt.get("scope_version") != t.current_version or
                receipt.get("upstream_candidate_id") != c.id or receipt.get("base_sha") != tip or
                not evidence.successful_execution(receipt.get("counts"), receipt.get("expected_test_ids"),
                    receipt.get("executed_test_ids"), receipt.get("commands"))):
                raise Invalid("required checks incomplete or UAC changed; new scope approval required")
            self._dep_change(s, actor, d, state="satisfied", accepted_scope_version=c.scope_version,
                accepted_candidate_id=c.id, integration_sha=c.integrated_sha,
                revalidation={"base_sha": tip, "report_artifact_id": report.id})
            append_message(s, project_id=t.project_id, ticket_id=t.id, thread_id="dependency:" + t.id,
                sender=actor.id, kind="system", body="Dependency required checks passed",
                attachment_ids=[report.id], meta={"intent": "dependency_revalidated", "upstream_ticket_id": up.id})
            remaining = s.scalar(select(Dependency.id).where(Dependency.ticket_id == t.id, Dependency.state != "satisfied"))
            return self._change(s, actor, t, "dependency_revalidated", blocker=self._work_blocker(t, bool(remaining)))

    @staticmethod
    def scope_digest(snapshot) -> str:
        return sha256_bytes(canonical_json(snapshot))

    def tip_known(self, project, sha) -> bool:
        """A release tip is the current accepted tip, an earlier accepted tip, or a verified release-sync candidate."""
        w = project.workflow
        if sha == w.get("accepted_tip") or sha in w.get("tip_history", []) or sha in w.get("release_tips", []):
            return True
        # The bounded display history is not the authoritative acceptance record. Accepted candidates and their
        # integration identity remain persisted even after thousands of later tickets move the tip.
        with self.db.read() as s:
            return s.scalar(select(Candidate.id).where(Candidate.project_id == project.id,
                Candidate.status == "accepted", Candidate.integrated_sha == sha).limit(1)) is not None

    @staticmethod
    def release_checklist(snapshot) -> list[str]:
        return sorted({key for entry in snapshot for key in entry.get("checklist", [])})

    def approve_release(self, actor, release_id, expected_revision, target_artifact_id, target_digest, evidence_ids,
                        manual_uac_ids=(), reviewed_diff_ids=()):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            r = self._row(s, Release, release_id, actor)
            if type(expected_revision) is not int or r.revision != expected_revision:
                raise RevisionConflict("releases", r.id, expected_revision, r.revision)
            if r.status != "draft" or r.target_artifact_id != target_artifact_id or r.target_digest != target_digest or set(evidence_ids) != set(r.evidence_artifact_ids) or len(evidence_ids) != len(set(evidence_ids)):
                raise Conflict("release target/evidence changed")
            project = s.get(Project, actor.project_id)
            # Tickets accepted after the freeze moved the tip on; the frozen tip must still be one the product accepted.
            if not self.tip_known(project, r.accepted_tip):
                raise Conflict("release accepted tip is not a tip this project accepted")
            required = self.release_checklist(r.scope_snapshot)
            if sorted(manual_uac_ids) != required or len(set(manual_uac_ids)) != len(list(manual_uac_ids)):
                raise Invalid("manual UAC checklist of this release must be confirmed exactly")
            for aid in [r.target_artifact_id, r.build_artifact_id, r.commit_artifact_id, r.context_artifact_id, *evidence_ids]:
                if aid:
                    evidence.artifact(s, self.store, r.project_id, aid)
            target_row = s.get(Artifact, r.target_artifact_id)
            if target_row.checksum != r.target_digest or not evidence_ids:
                raise Invalid("release digest/evidence missing")
            # DEV-014 verification service publishes a bound combined regression receipt.
            target_doc = evidence.document(s, self.store, r.target_artifact_id)
            if (target_doc.get("accepted_tip") != r.accepted_tip or target_doc.get("build_artifact_id") != r.build_artifact_id
                    or target_doc.get("scope_digest") != self.scope_digest(r.scope_snapshot)):
                raise Conflict("release manifest does not identify its frozen build/tip/scope")
            diffs = target_doc.get("technical_review_evidence_ids", [])
            if target_doc.get("sync") and (len(diffs) != 2 or len(set(diffs)) != 2):
                raise Invalid("synchronised release requires pinned diffs for technical review")
            if sorted(reviewed_diff_ids) != sorted(diffs) or len(set(reviewed_diff_ids)) != len(list(reviewed_diff_ids)):
                raise Invalid("technical review of the exact synchronisation diffs must be confirmed")
            if not set(diffs).issubset(evidence_ids):
                raise Invalid("technical review diffs must be included in release evidence")
            receipts = [evidence.document(s, self.store, aid) for aid in evidence_ids
                        if s.get(Artifact, aid).kind == "report" and s.get(Artifact, aid).meta.get("producer") == "verification"]
            if not any(p.get("kind") == "release_verification" and p.get("status") == "passed" and
                p.get("target_artifact_id") == r.target_artifact_id and p.get("target_digest") == r.target_digest and
                p.get("accepted_tip") == r.accepted_tip and p.get("fake_provider") is False and
                p.get("infrastructure_failure") is False and evidence.successful_execution(
                    p.get("counts"), p.get("expected_test_ids"), p.get("executed_test_ids"), p.get("commands")) for p in receipts):
                raise Invalid("release requires matching combined verification receipt")
            s.add(Approval(project_id=r.project_id, type="release", user_id=actor.id, release_id=r.id,
                target_artifact_id=r.target_artifact_id, target_digest=r.target_digest, evidence_artifact_ids=list(evidence_ids),
                details={"manual_uac_ids": required, "scope_digest": self.scope_digest(r.scope_snapshot),
                         "accepted_tip": r.accepted_tip, **({"technical_review": {
                             "reviewer": actor.id, "diff_artifact_ids": list(diffs), "target_digest": r.target_digest}}
                             if diffs else {})}))
            s.flush()
            r.status = "approved"
            s.flush()
            self._event(s, actor, "release.approved", r, {"target_artifact_id": r.target_artifact_id, "target_digest": r.target_digest})
            return r

    def freeze_release_scope(self, actor, *, include_released=False):
        """User intent to cut a release: the accepted tip and the accepted tickets not yet in an approved release.

        Nothing is built or approved here. Returns (accepted_tip, entries); the caller records the freeze as a job."""
        with self.db.read() as s:
            self._permit(s, actor, "user")
            project = self._row(s, Project, actor.project_id, actor)
            tip = project.workflow.get("accepted_tip")
            if not tip:
                raise Conflict("project has no accepted base yet")
            if s.scalar(select(Ticket.id).where(Ticket.project_id == project.id, Ticket.phase == "integrating")):
                raise Conflict("reconcile pending integrations before freezing a release")
            if s.scalar(select(Release.id).where(Release.project_id == project.id, Release.status == "draft")):
                raise Conflict("a draft release already exists; approve or discard it first")
            released = {e["ticket_id"] for r in s.scalars(select(Release).where(
                Release.project_id == project.id, Release.status.in_(("approved", "exported", "deployed"))))
                for e in r.scope_snapshot}
            entries = []
            for t in s.scalars(select(Ticket).where(Ticket.project_id == project.id, Ticket.phase == "accepted").order_by(Ticket.number)):
                c = s.get(Candidate, t.workflow.get("accepted_candidate_id")) if t.workflow.get("accepted_candidate_id") else None
                if (t.id in released and not include_released) or c is None or c.status != "accepted" or not c.integrated_sha:
                    continue
                version = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == t.id,
                                                               TicketVersion.version == c.scope_version))
                approval = s.scalar(select(Approval).where(Approval.type == "uat", Approval.candidate_id == c.id))
                from .qa_resolution import waiver_for
                qa_decision = waiver_for(s, c)
                manual_ids = set(qa_decision.manual_uac_ids) if qa_decision else set()
                entries.append({"ticket_id": t.id, "number": t.number, "title": version.title, "scope_version": c.scope_version,
                    "candidate_id": c.id, "integrated_sha": c.integrated_sha, "uat_approval_id": approval.id if approval else None,
                    "target_artifact_id": c.target_artifact_id, "target_digest": c.target_digest,
                    "uac": [{"id": u["id"], "text": u["text"], "mode": "manual" if u['id'] in manual_ids else u.get("mode", "automated")} for u in version.uac],
                    "qa_waiver": {'id': qa_decision.id, 'reason': qa_decision.reason,
                                  'manual_uac_ids': qa_decision.manual_uac_ids,
                                  'excluded_test_ids': qa_decision.excluded_test_ids} if qa_decision else None,
                    "checklist": [f"{t.id}:{u['id']}" for u in version.uac if u.get("mode") == "manual" or u['id'] in manual_ids]})
            if not entries:
                raise Invalid("no accepted tickets are waiting for a release")
            return tip, entries

    def draft_release(self, actor, *, scope_snapshot, accepted_tip, target_artifact_id, target_digest, build_artifact_id,
                      evidence_ids, verification_passed, commit_artifact_id=None, sync_candidate=False, replaces=None):
        """Verification service publishes the release verified on ONE combined target. A failed regression is still
        recorded (status failed) with its evidence, so the user sees why; it can never be approved."""
        with self.db.write() as s:
            self._permit(s, actor, "verification")
            project = self._row(s, Project, actor.project_id, actor)
            drafts = list(s.scalars(select(Release.id).where(Release.project_id == project.id, Release.status == "draft")))
            if drafts and drafts != [replaces]:
                raise Conflict("a draft release already exists")
            old = self._row(s, Release, replaces, actor) if replaces else None
            if old is not None and old.status not in ("draft", "approved"):
                raise Conflict("the source release is no longer eligible for replacement")
            evidence.digest(accepted_tip, (40, 64))
            if not sync_candidate and not self.tip_known(project, accepted_tip):
                raise Conflict("release tip is not a tip this project accepted")
            if not isinstance(scope_snapshot, list) or not scope_snapshot or not evidence_ids:
                raise Invalid("a release needs its frozen scope and verification evidence")
            for aid in [target_artifact_id, build_artifact_id, commit_artifact_id, *evidence_ids]:
                if aid:
                    evidence.artifact(s, self.store, project.id, aid)
            target = evidence.document(s, self.store, target_artifact_id)
            if (target.get("accepted_tip") != accepted_tip or target.get("build_artifact_id") != build_artifact_id
                    or target.get("scope_digest") != self.scope_digest(scope_snapshot)):
                raise Conflict("release target does not identify the frozen tip/build/scope")
            if sync_candidate:
                # The combined candidate becomes a valid release tip only through its own verification evidence.
                sync = [evidence.document(s, self.store, aid) for aid in evidence_ids
                        if s.get(Artifact, aid).kind == "report" and s.get(Artifact, aid).meta.get("producer") == "verification"]
                if not any(d.get("kind") == "release_sync" and d.get("candidate_sha") == accepted_tip for d in sync):
                    raise Invalid("sync candidate has no verification receipt")
            r = Release(project_id=project.id, scope_snapshot=scope_snapshot, accepted_tip=accepted_tip,
                        target_artifact_id=target_artifact_id, target_digest=target_digest, build_artifact_id=build_artifact_id,
                        commit_artifact_id=commit_artifact_id, evidence_artifact_ids=list(evidence_ids))
            s.add(r)
            s.flush()
            if sync_candidate:
                apply_change(s, Project, project.id, expected_revision=project.revision, values={"workflow": {
                    **project.workflow, "release_tips": [*project.workflow.get("release_tips", []), accepted_tip][-200:]}},
                    event=EventSpec("project.release_tip_recorded", actor.id, {"release_id": r.id, "tip": accepted_tip}))
            if replaces:
                if old.status == "draft":
                    old.status = "failed"
                    s.flush()
                    self._event(s, actor, "release.superseded", old, {"replaced_by": r.id})
            self._event(s, actor, "release.drafted", r, {"accepted_tip": accepted_tip, "target_digest": target_digest,
                                                          "verification_passed": bool(verification_passed)})
            if not verification_passed:
                r.status = "failed"  # never approvable; the evidence stays attached
                s.flush()
                self._event(s, actor, "release.failed", r, {"reason": "combined verification did not pass"})
            return r

    def discard_release(self, actor, release_id, expected_revision):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            r = self._row(s, Release, release_id, actor)
            if r.revision != expected_revision:
                raise RevisionConflict("releases", r.id, expected_revision, r.revision)
            if r.status != "draft":
                raise Conflict("only a draft release can be discarded")
            r.status = "failed"
            s.flush()
            self._event(s, actor, "release.discarded", r, {})
            return r

    def record_export(self, actor, release_id, expected_revision, export_result):
        """Integrator receipt: local export artifacts exist for exactly the approved target. Never push or deploy."""
        with self.db.write() as s:
            self._permit(s, actor, "integrator")
            r = self._row(s, Release, release_id, actor)
            if r.revision != expected_revision:
                raise RevisionConflict("releases", r.id, expected_revision, r.revision)
            if r.status != "approved":
                raise Conflict("only an approved release can be exported")
            if (not isinstance(export_result, dict) or export_result.get("tip") != r.accepted_tip
                    or export_result.get("pushed") is not False or export_result.get("deployed") is not False):
                raise Invalid("export receipt must name the release tip and state that nothing was pushed or deployed")
            for key in ("patch_artifact_id", "bundle_artifact_id"):
                row = evidence.artifact(s, self.store, r.project_id, export_result.get(key))
                if row.meta.get("producer") != "integrator":
                    raise Invalid("export artifacts must be published by the integrator")
            r.export_result, r.status = export_result, "exported"
            s.flush()
            self._event(s, actor, "release.exported", r, {"patch_artifact_id": export_result["patch_artifact_id"],
                                                          "bundle_artifact_id": export_result["bundle_artifact_id"]})
            return r

    def waive_baseline(self, actor, ticket_id, expected_revision, fingerprint_artifact_id, reason):
        with self.db.write() as s:
            self._permit(s, actor, "user")
            t = self._ticket(s, actor, ticket_id, expected_revision, ("scope_review", "ready", "development", "technical_review", "qa", "uat"))
            fingerprint = evidence.artifact(s, self.store, t.project_id, fingerprint_artifact_id, "report")
            if fingerprint.meta.get("producer") != "verification":
                raise Invalid("baseline fingerprint must come from verification service")
            fp = evidence.document(s, self.store, fingerprint.id)
            if (fp.get("kind") != "baseline_failure" or fp.get("ticket_id") != t.id or fp.get("scope_version") != t.current_version or
                fp.get("category") != "baseline" or fp.get("uac_ids") != [] or fp.get("infrastructure_failure") is not False or
                not fp.get("test_id") or not fp.get("signature") or not fp.get("environment") or not reason):
                raise Invalid("waiver cannot cover UAC/infrastructure or unspecific baseline")
            evidence.digest(fp.get("base_sha"), (40, 64))
            evidence.digest(fp.get("environment_digest"))
            if fp["base_sha"] != s.get(Project, t.project_id).workflow.get("accepted_tip"):
                raise Conflict("waiver baseline base changed")
            approval = Approval(project_id=t.project_id, type="baseline_waiver", user_id=actor.id,
                ticket_id=t.id, scope_version=t.current_version, target_artifact_id=fingerprint.id,
                target_digest=fingerprint.checksum, details={**fp, "reason": reason, "status": "waived"})
            s.add(approval)
            s.flush()
            self._change(s, actor, t, "baseline_waived")
            return approval

    def waiver_matches(self, actor, approval_id, *, ticket_id, scope_version, base_sha, environment_digest, test_id, signature):
        with self.db.read() as s:
            self._permit(s, actor, "user", "verification")
            ap = self._row(s, Approval, approval_id, actor)
            evidence.artifact(s, self.store, ap.project_id, ap.target_artifact_id, "report")
            wanted = dict(ticket_id=ticket_id, scope_version=scope_version, base_sha=base_sha,
                environment_digest=environment_digest, test_id=test_id, signature=signature)
            return ap.type == "baseline_waiver" and all(ap.details.get(k) == v for k, v in wanted.items())

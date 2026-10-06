from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Body):
    code: str = Field(min_length=16, max_length=256)


class Empty(Body):
    """A command with no parameters. Stop is an idempotent revoke: it must never be blocked by a
    revision that a normal supervisor heartbeat advances every few seconds."""


class Revision(Body):
    expected_revision: int = Field(ge=1)


class ProjectCreate(Body):
    name: str = Field(min_length=1, max_length=200)
    mode: Literal["new", "existing"] = "new"
    brief: str = Field(default="", max_length=30000)
    repo_ref: str | None = Field(default=None, max_length=2000)


class Brief(Revision):
    brief: str = Field(max_length=30000)


class Onboarding(Revision):
    manifest: dict[str, Any]
    patch: str | None = Field(default=None, max_length=1000000)
    source_sha: str | None = Field(default=None, max_length=40)


class Criterion(Body):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)
    mode: Literal["automated", "manual"] = "automated"


class Scope(Body):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=15000)
    uac: list[Criterion] = Field(min_length=1, max_length=100)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    reverts_candidate_id: str | None = None


class ScopeEdit(Revision):
    document: Scope


class ApprovalEntry(Revision):
    ticket_id: str
    scope_version: int = Field(ge=1)


class ApprovalBatch(Body):
    items: list[ApprovalEntry] = Field(min_length=1, max_length=100)


class Priority(Revision):
    priority: int = Field(ge=-10000, le=10000)


class ProposalDecision(Revision):
    accept: bool


class Decision(Body):
    accept: bool


class MessageCreate(Body):
    # Accepted for older clients, but append-only chat has no project revision precondition.
    expected_revision: int | None = Field(default=None, ge=1)
    body: str = Field(min_length=1, max_length=8000)
    task: Literal["breakdown", "revise", "note"] = "breakdown"
    ticket_id: str | None = None


class InputAnswer(Revision):
    request_id: str
    scope_version: int | None
    generation: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=8000)


class NonblockingAnswer(Body):
    scope_version: int | None
    generation: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=8000)


class Changes(Revision):
    candidate_id: str
    reason: str = Field(min_length=1, max_length=8000)


class Repair(Revision):
    additional_cycles: int = Field(ge=1, le=3)


class Budget(Revision):
    additions: dict[str, int | float]
    unlimited_total_tokens: bool = False
    unlimited_budgets: bool = False


class Uat(Revision):
    candidate_id: str
    scope_version: int = Field(ge=1)
    target_artifact_id: str
    target_digest: str
    verification_id: str
    evidence_ids: list[str]
    manual_uac_ids: list[str] = Field(default_factory=list)


class Waiver(Revision):
    ticket_id: str
    fingerprint_artifact_id: str
    reason: str = Field(min_length=1, max_length=4000)


class ReleaseDecision(Revision):
    target_artifact_id: str
    target_digest: str
    evidence_ids: list[str]
    manual_uac_ids: list[str] = Field(default_factory=list, max_length=500)
    reviewed_diff_ids: list[str] = Field(default_factory=list, max_length=2)


class CandidateSubmit(Revision):
    commit_artifact_id: str
    commit_receipt_id: str
    base_sha: str


class CandidateReview(Revision):
    ticket_id: str
    accept: bool
    reason: str = Field(default="", max_length=8000)


class Tool(Body):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)

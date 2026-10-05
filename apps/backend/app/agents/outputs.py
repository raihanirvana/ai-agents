"""Structured output contracts for PO and lead calls, validated before anything is persisted.

Models may only say what these shapes allow: extra fields are rejected, sizes are capped, ids are
checked, and a breakdown must be a real acyclic dependency graph. Validation errors are surfaced
(never silently repaired) so an invalid answer is visible and counted.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

KEY = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$"
ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UAC(Contract):
    id: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=400)
    mode: Literal["automated", "manual"] = "automated"


def _unique(items: list, label: str) -> list:
    if len(set(items)) != len(items):
        raise ValueError(f"duplicate {label}")
    return items


class TicketProposal(Contract):
    key: str = Field(pattern=KEY)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    uac: list[UAC] = Field(min_length=1, max_length=20)
    depends_on_keys: list[str] = Field(default_factory=list, max_length=12)
    depends_on_ticket_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("uac")
    @classmethod
    def _unique_uac(cls, value):
        _unique([c.id for c in value], "UAC id")
        return value

    @field_validator("depends_on_keys", "depends_on_ticket_ids")
    @classmethod
    def _unique_deps(cls, value):
        return _unique(value, "dependency")


class PoProposal(Contract):
    kind: Literal["proposal"]
    summary: str = Field(min_length=1, max_length=1000)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    tickets: list[TicketProposal] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _graph(self):
        keys = [t.key for t in self.tickets]
        _unique(keys, "ticket key")
        graph = {t.key: t.depends_on_keys for t in self.tickets}
        for key, deps in graph.items():
            if key in deps:
                raise ValueError(f"ticket {key} depends on itself")
            unknown = [d for d in deps if d not in graph]
            if unknown:
                raise ValueError(f"ticket {key} depends on unknown key {unknown[0]}")
        order, state = [], {}

        def visit(key):
            if state.get(key) == 1:
                raise ValueError("dependency cycle between tickets")
            if state.get(key) == 2:
                return
            state[key] = 1
            for dep in graph[key]:
                visit(dep)
            state[key] = 2
            order.append(key)

        for key in graph:
            visit(key)
        return self

    def creation_order(self) -> list[TicketProposal]:
        """Tickets with prerequisites first (the graph was validated acyclic)."""
        by_key, done, order = {t.key: t for t in self.tickets}, set(), []

        def visit(key):
            if key in done:
                return
            for dep in by_key[key].depends_on_keys:
                visit(dep)
            done.add(key)
            order.append(by_key[key])

        for t in self.tickets:
            visit(t.key)
        return order


class PoRevision(Contract):
    kind: Literal["revision"]
    summary: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    uac: list[UAC] = Field(min_length=1, max_length=20)
    depends_on_ticket_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("uac")
    @classmethod
    def _unique_uac(cls, value):
        _unique([c.id for c in value], "UAC id")
        return value


class Question(Contract):
    id: str = Field(pattern=KEY)
    question: str = Field(min_length=1, max_length=400)
    why: str = Field(default="", max_length=400)


class Clarification(Contract):
    kind: Literal["clarification"]
    questions: list[Question] = Field(min_length=1, max_length=5)

    @field_validator("questions")
    @classmethod
    def _unique_ids(cls, value):
        _unique([q.id for q in value], "question id")
        return value

    def as_text(self) -> str:
        return "\n".join(f"{i}. {q.question}" + (f" ({q.why})" if q.why else "")
                         for i, q in enumerate(self.questions, 1))


class PlanStep(Contract):
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=1500)
    files: list[str] = Field(default_factory=list, max_length=30)


class DecisionProposal(Contract):
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1500)


class LeadPlan(Contract):
    kind: Literal["technical_plan"]
    summary: str = Field(min_length=1, max_length=1500)
    steps: list[PlanStep] = Field(min_length=1, max_length=25)
    decisions: list[DecisionProposal] = Field(default_factory=list, max_length=10)
    risks: list[str] = Field(default_factory=list, max_length=10)
    needs_user: bool = False


class LeadAnswer(Contract):
    kind: Literal["answer"]
    outcome: Literal["proceed", "change", "needs_user"]
    answer: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(default="", max_length=1000)


PoOutput = Annotated[Union[PoProposal, PoRevision, Clarification], Field(discriminator="kind")]
PoReviseOutput = Annotated[Union[PoRevision, Clarification], Field(discriminator="kind")]
LeadPlanOutput = Annotated[Union[LeadPlan, Clarification], Field(discriminator="kind")]


class InvalidOutput(ValueError):
    """The model answer is not valid for the contract; errors are safe to show (no raw secrets)."""

    def __init__(self, errors: list[str], raw_excerpt: str = ""):
        super().__init__("; ".join(errors)[:600])
        self.errors, self.raw_excerpt = errors, raw_excerpt


_FENCE = re.compile(r"^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$")


def parse_output(text: str, union) -> BaseModel:
    """Parse a model answer (optionally in a code fence) into one of the allowed shapes."""
    from pydantic import TypeAdapter
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise InvalidOutput([f"not valid JSON: {exc}"], body[:200]) from exc
    if not isinstance(data, dict):
        raise InvalidOutput(["the answer must be a JSON object"], body[:200])
    try:
        return TypeAdapter(union).validate_python(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8]]
        raise InvalidOutput(errors, body[:200]) from exc

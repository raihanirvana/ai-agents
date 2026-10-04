from dataclasses import dataclass


class DomainError(RuntimeError):
    pass


class Forbidden(DomainError):
    pass


class Invalid(DomainError):
    pass


class Conflict(DomainError):
    pass


@dataclass(frozen=True)
class Actor:
    """Constructed by trusted authentication/worker binding, never from model output.

    Agents additionally bind project/job/generation. Services are named capabilities,
    not a universal system actor. Public transport must not accept an arbitrary Actor.
    """
    id: str
    role: str
    project_id: str
    job_id: str | None = None
    generation: int | None = None


@dataclass(frozen=True)
class ApprovalItem:
    ticket_id: str
    scope_version: int
    expected_revision: int


@dataclass(frozen=True)
class Attempt:
    job_id: str
    generation: int
    scope_version: int

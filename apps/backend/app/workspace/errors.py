"""Error types for the workspace/sandbox layer."""


class WorkspaceError(Exception):
    """Base class; every failure in this package derives from it."""


class AuthorizationError(WorkspaceError):
    """Caller identity, credential, generation, lease, or role does not permit the call."""


class PathViolation(WorkspaceError):
    """A path or file type from untrusted sandbox content is not allowed."""


class LimitExceeded(WorkspaceError):
    """Untrusted content exceeded a finite file-count or size bound."""


class GitBrokerError(WorkspaceError):
    """A Git operation failed or was refused by broker policy."""


class ManifestError(WorkspaceError):
    """A runner manifest failed validation."""


class SandboxError(WorkspaceError):
    """Container creation, execution, or cleanup failed."""

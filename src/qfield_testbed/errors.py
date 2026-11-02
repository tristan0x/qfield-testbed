"""Exception hierarchy. The CLI maps these to exit codes, error codes and user-facing messages.

Every failure raised on purpose is a subclass of one of the intermediate classes below, which fix the exit code
and the default error code of the machine contract (docs/adr/0001-agent-interface.md). Raise a typed subclass,
never :class:`TestbedError` itself.
"""

from __future__ import annotations

from typing import Any


class TestbedError(Exception):
    """Base class: a failure the user can act on, reported without a traceback.

    ``hint`` is the next thing to try, ``docs`` a topic of the bundled documentation, ``details`` structured
    facts for machine consumers (failing probe, log tail…).
    """

    code = "ERROR"
    exit_code = 1

    def __init__(
        self,
        message: str = "",
        *,
        hint: str | None = None,
        docs: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.docs = docs
        self.details = details or {}


# --- intermediate classes: one per exit code -------------------------------------------------------------------


class UsageError(TestbedError):
    """The command line, a spec or a scenario is wrong."""

    code = "USAGE"
    exit_code = 2


class PrerequisiteMissingError(TestbedError):
    """The host, the configuration or a previous step lacks something; often a human has to act."""

    code = "PREREQ_MISSING"
    exit_code = 3


class ResourceExhaustedError(TestbedError):
    """No free slot, port or disk space."""

    code = "RESOURCE_EXHAUSTED"
    exit_code = 4


class NotFoundError(TestbedError):
    """A deployment, session, ref, path or file does not exist."""

    code = "NOT_FOUND"
    exit_code = 5


class TimeoutExceededError(TestbedError):
    """Something did not become ready in time."""

    code = "TIMEOUT"
    exit_code = 6


class ComponentFailureError(TestbedError):
    """A component, a build or an external command failed."""

    code = "COMPONENT_FAILED"
    exit_code = 7


class RefusedError(TestbedError):
    """The operation needs a confirmation or an authorization that was not given."""

    code = "REFUSED"
    exit_code = 8


# --- specific errors -------------------------------------------------------------------------------------------


class SpecError(UsageError):
    """The deployment spec or bisect scenario is invalid."""

    code = "INVALID_SPEC"


class AlreadyExistsError(UsageError):
    """Something with that name exists already."""

    code = "ALREADY_EXISTS"


class DeploymentExistsError(AlreadyExistsError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"A deployment named '{name}' already exists.",
            hint="remove it first (`qft deployment remove`) or pick another name (--name)",
        )


class NeedsHumanError(PrerequisiteMissingError):
    """Only a person at a terminal can go on (manual bisect verdicts)."""

    code = "NEEDS_HUMAN"


class UnsupportedComponentError(PrerequisiteMissingError):
    """The spec names a component this version of the testbed cannot run (yet, or on this host)."""


class UnknownDeploymentError(NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"No deployment named '{name}'.", hint="run `qft deployment list`")


class NoFreeSlotError(ResourceExhaustedError):
    def __init__(self, max_slots: int) -> None:
        super().__init__(
            f"All {max_slots} slots are taken or their port blocks are busy.",
            hint="remove a deployment, or raise `max_slots` / change `port_base` in the config",
            docs="isolation",
        )


class HealthTimeoutError(TimeoutExceededError):
    """A component did not become healthy in time."""


class MaterializationUnavailableError(ComponentFailureError):
    """A revision cannot be fetched or built; bisect treats this as a skipped candidate."""


class WiringError(ComponentFailureError):
    """A component is up, but pointing clients at it (test account, demo project) failed."""


class CommandError(ComponentFailureError):
    """An external command failed; the message carries its stderr tail."""

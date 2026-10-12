"""Exception hierarchy. The CLI maps these to exit codes and user-facing messages."""


class TestbedError(Exception):
    """Base class: a failure the user can act on, reported without a traceback."""

    exit_code = 1


class SpecError(TestbedError):
    """The deployment spec or bisect scenario is invalid."""

    exit_code = 2


class UnknownDeploymentError(TestbedError):
    def __init__(self, name: str) -> None:
        super().__init__(f"No deployment named '{name}'. Run `qft deployment list`.")


class DeploymentExistsError(TestbedError):
    def __init__(self, name: str) -> None:
        super().__init__(f"A deployment named '{name}' already exists. Remove it first or pick another name.")


class UnsupportedComponentError(TestbedError):
    """The spec names a component this version of the testbed cannot run (yet, or on this host)."""


class NoFreeSlotError(TestbedError):
    def __init__(self, max_slots: int) -> None:
        super().__init__(
            f"All {max_slots} slots are taken or their port blocks are busy. "
            "Remove a deployment, or raise `max_slots` / change `port_base` in the config."
        )


class MaterializationUnavailableError(TestbedError):
    """A revision cannot be fetched or built; bisect treats this as a skipped candidate."""

    exit_code = 125


class HealthTimeoutError(TestbedError):
    """A component did not become healthy in time."""


class CommandError(TestbedError):
    """An external command failed; the message carries its stderr tail."""

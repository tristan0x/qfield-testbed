"""The ``qft`` command line (noun–verb: `qft deployment add`, `qft bisect run`, `qft cache prune`…)."""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Any, NoReturn

import typer
from rich.logging import RichHandler

from qfield_testbed import __version__
from qfield_testbed.caller import set_agent_session
from qfield_testbed.cli import agent, bisect, cache, config, deployment, docs, doctor, gc, mcp, spec
from qfield_testbed.cli._console import (
    OUTPUT,
    console,
    emit,
    emit_default,
    emit_error,
    error,
    json_requested,
    non_interactive_requested,
    print_hint,
    set_json_output,
    stderr,
)
from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import RefusedError, TestbedError
from qfield_testbed.gc import apply_gc, plan_gc

INTERRUPTED_EXIT_CODE = 130

app = typer.Typer(
    name="qft",
    help="Sandboxed deployments of the QField stack (QGIS, QFieldSync, QFieldCloud, QField) and bisect sessions.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(deployment.app, name="deployment", help="Add, start, stop, inspect and remove deployments.")
app.add_typer(bisect.app, name="bisect", help="Find the revision of a component that introduced a regression.")
app.add_typer(cache.app, name="cache", help="Inspect and prune the shared cache of materializations.")
app.add_typer(config.app, name="config", help="Show the global configuration.")
app.add_typer(spec.app, name="spec", help="Help writing deployment specs.")
app.add_typer(agent.app, name="agent", help="Set up coding agents to drive qft.")
app.add_typer(mcp.app, name="mcp", help="Serve the qft tools to MCP clients.")
app.command()(doctor.doctor)
app.command()(docs.docs)
app.command()(gc.gc)


def _version(value: bool) -> None:
    if value:
        emit({"version": __version__}, human=lambda: console.print(f"qfield-testbed {__version__}"))
        raise typer.Exit


@app.callback()
def root(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="-v for info, -vv for commands")] = 0,
    json_: Annotated[
        bool,
        typer.Option("--json", help="One JSON document on stdout (the envelope); also QFT_OUTPUT=json"),
    ] = False,
    agent_session: Annotated[
        str | None,
        typer.Option(
            "--agent-session",
            envvar="QFT_AGENT_SESSION",
            help="Act for this agent session: owns what it creates, may only stop or remove its own",
        ),
    ] = None,
    non_interactive: Annotated[
        bool,
        typer.Option("--non-interactive", help="Never prompt: refuse what needs a person; also QFT_NONINTERACTIVE=1"),
    ] = False,
    _: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version")] = False,
) -> None:
    set_json_output(json_ or OUTPUT.requested or os.environ.get("QFT_OUTPUT", "").lower() == "json")
    OUTPUT.non_interactive = non_interactive or non_interactive_requested()
    set_agent_session(agent_session)
    _opportunistic_gc()
    level = logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="%H:%M:%S",
        # stderr in every mode: stdout is for results (tables, `env` exports, the JSON envelope)
        handlers=[RichHandler(console=stderr, show_path=False, show_time=verbose > 1, markup=False)],
    )


def _opportunistic_gc() -> None:
    """With `gc_on_start: true`, every invocation first removes expired deployments, quietly and never blocking."""
    try:
        config = GlobalConfig.load()
        if not config.gc_on_start:
            return
        testbed = Testbed(config)
        plan = plan_gc(testbed, expired=True)
        if plan.expired:
            report = apply_gc(testbed, plan)
            logging.getLogger(__name__).info("gc_on_start removed %s", ", ".join(report.removed) or "nothing")
    except Exception:
        logging.getLogger(__name__).debug("gc_on_start failed", exc_info=True)


def _fail(
    code: str,
    message: str,
    exit_code: int,
    *,
    hint: str | None = None,
    docs: str | None = None,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    if OUTPUT.json:
        emit_error(code, message, hint=hint, docs=docs, details=details)
    else:
        error(message)
        if hint:
            print_hint(hint)
    sys.exit(exit_code)


def main() -> None:
    OUTPUT.requested = json_requested(sys.argv[1:])
    # before parsing: a usage error on the root command must already answer in JSON
    set_json_output(OUTPUT.requested)
    try:
        # outside standalone mode click *returns* the code of a `typer.Exit` raised by a command
        code = app(standalone_mode=False)
    except TestbedError as exc:
        _fail(exc.code, exc.message or str(exc), exc.exit_code, hint=exc.hint, docs=exc.docs, details=exc.details)
    except typer.Exit as exc:
        code = exc.exit_code
    except typer.TyperException as exc:
        # usage errors (missing argument, unknown option, no command) are re-raised outside standalone mode:
        # print click's own usage message instead of a traceback
        exit_code = getattr(exc, "exit_code", 2)
        show = getattr(exc, "show", None)
        if OUTPUT.json or not callable(show):
            _fail("USAGE", exc.format_message(), exit_code)
        show()
        sys.exit(exit_code)
    except typer.Abort:
        _fail("REFUSED", "aborted", RefusedError.exit_code)
    except KeyboardInterrupt:
        _fail("INTERRUPTED", "interrupted", INTERRUPTED_EXIT_CODE)
    if not code:
        emit_default()
    if isinstance(code, int) and code:
        sys.exit(code)

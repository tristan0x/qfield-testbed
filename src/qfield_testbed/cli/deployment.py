from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from qfield_testbed.caller import CALLER
from qfield_testbed.cli._console import (
    OUTPUT,
    confirm,
    console,
    emit,
    ok,
    set_json_output,
    styled_status,
    warn,
)
from qfield_testbed.components.qgis import qt_platform
from qfield_testbed.deployment import DeploymentInfo, DeploymentSummary, Testbed, parse_ttl
from qfield_testbed.durations import parse_duration
from qfield_testbed.envfmt import FORMATS, render
from qfield_testbed.errors import NotFoundError, TimeoutExceededError, UsageError
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import OWNERS

app = typer.Typer(no_args_is_help=True)

NameArg = Annotated[str, typer.Argument(help="Deployment name", show_default=False)]
ComponentOpt = Annotated[str | None, typer.Option("--component", "-c", help="Act on a single component")]
TimeoutOpt = Annotated[
    str | None,
    typer.Option("--timeout", help="Fail (exit 6) unless every component is ready within e.g. 10m, 90s"),
]


ForceOpt = Annotated[
    bool, typer.Option("--force", "-f", help="Act on a deployment another session owns (agent sessions only)")
]
TtlOpt = Annotated[
    str | None,
    typer.Option("--ttl", help="Removable by `qft gc` this long after its last use, e.g. 8h, 3d, or none"),
]


def _seconds(duration: str | None) -> float | None:
    return parse_duration(duration).total_seconds() if duration else None


def _parse_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--set expects key=value, got '{item}'")
        overrides[key] = value
    return overrides


@app.command()
def add(
    spec_file: Annotated[Path, typer.Argument(help="Deployment spec (YAML); `-` reads stdin", show_default=False)],
    *,
    name: Annotated[str | None, typer.Option("--name", "-n", help="Override the spec's name")] = None,
    set_: Annotated[
        list[str], typer.Option("--set", "-s", help="Override a spec value, e.g. qfieldsync.version=release:v4.22.3")
    ] = [],  # noqa: B006
    start: Annotated[bool, typer.Option("--start", help="Start the deployment right away")] = False,
    timeout: TimeoutOpt = None,
    ttl: TtlOpt = None,
) -> None:
    """Register a deployment from a spec and allocate its slot (ports, network, directory)."""
    spec = _read_spec(spec_file, name, set_)
    testbed = Testbed()
    deployment = testbed.add(
        spec, agent_session=CALLER.agent_session, ttl=parse_ttl(ttl) if ttl is not None else "default"
    )
    ok(
        f"deployment [bold]{spec.name}[/] registered on slot {deployment.state.slot} "
        f"(ports {deployment.ports.base}–{deployment.ports.base + 99})"
    )
    if start:
        deployment.start(timeout=_seconds(timeout))
        ok(f"deployment [bold]{spec.name}[/] is running")
        info = deployment.info()
        emit(info, human=lambda: _print_show(info))
    else:
        emit(deployment.info(), human=lambda: console.print(f"Next: [bold]qft deployment start {spec.name}[/]"))


def _read_spec(spec_file: Path, name: str | None, set_: list[str]) -> DeploymentSpec:
    overrides = _parse_overrides(set_)
    if name:
        overrides["name"] = name
    if str(spec_file) == "-":
        return DeploymentSpec.from_yaml(typer.get_text_stream("stdin").read(), overrides=overrides)
    return DeploymentSpec.from_file(spec_file, overrides=overrides)


@app.command()
def ensure(
    spec_file: Annotated[Path, typer.Argument(help="Deployment spec (YAML); `-` reads stdin", show_default=False)],
    *,
    name: Annotated[
        str | None, typer.Option("--name", "-n", help="Override the spec's name; `auto` names it after the spec")
    ] = None,
    set_: Annotated[list[str], typer.Option("--set", "-s", help="Override a spec value, as for `add`")] = [],  # noqa: B006
    replace: Annotated[
        bool, typer.Option("--replace", help="Recreate a deployment of that name made from another spec")
    ] = False,
    timeout: TimeoutOpt = None,
    ttl: TtlOpt = None,
    force: ForceOpt = False,
) -> None:
    """Make sure a deployment of this spec exists and is ready: reuse it if it does, create and start it if not."""
    auto = name == "auto"
    spec = _read_spec(spec_file, None if auto else name, set_)
    result = Testbed().ensure(
        spec,
        auto_name=auto,
        replace=replace,
        force=force,
        timeout=_seconds(timeout),
        ttl=parse_ttl(ttl) if ttl is not None else "default",
    )
    for message in result.warnings:
        warn(message)
    verb = "created" if result.created else "started" if result.started else "reused"
    ok(f"deployment [bold]{result.info.name}[/] {verb} (spec {result.spec_hash})")
    emit(
        {
            **result.info.model_dump(mode="json"),
            "created": result.created,
            "started": result.started,
            "spec_hash": result.spec_hash,
        },
        human=lambda: _print_show(result.info),
    )


@app.command("list")
def list_(
    agent_session: Annotated[
        str | None, typer.Option("--agent-session", help="Only the deployments of this agent session")
    ] = None,
    owner: Annotated[str | None, typer.Option("--owner", help="Only those owned by: human, agent or bisect")] = None,
) -> None:
    """List deployments with their slot, status, components, owner and expiry."""
    if owner is not None and owner not in OWNERS:
        raise UsageError(f"unknown owner '{owner}'", hint="one of: " + ", ".join(OWNERS))
    testbed = Testbed()
    summaries = [
        summary
        for summary in (deployment.summary() for deployment in testbed.deployments())
        if (agent_session is None or summary.agent_session == agent_session)
        and (owner is None or summary.owner == owner)
    ]
    emit({"deployments": summaries}, human=lambda: _print_list(summaries))


def _print_list(summaries: list[DeploymentSummary]) -> None:
    if not summaries:
        console.print("No deployment yet. Try: [bold]qft deployment add examples/minimal.yaml --start[/]")
        return
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("NAME")
    table.add_column("SLOT", justify="right")
    table.add_column("PORTS")
    table.add_column("STATUS")
    table.add_column("COMPONENTS")
    table.add_column("OWNER")
    table.add_column("EXPIRES")
    for summary in summaries:
        components = "  ".join(f"{name} {styled_status(status)}" for name, status in summary.components.items())
        session = summary.session or summary.agent_session
        table.add_row(
            summary.name,
            str(summary.slot),
            f"{summary.port_base}–{summary.port_last}",
            styled_status(summary.status),
            components,
            f"{summary.owner} {session}" if session else summary.owner,
            summary.expires_at.astimezone().strftime("%Y-%m-%d %H:%M") if summary.expires_at else "",
        )
    console.print(table)


def _print_show(info: DeploymentInfo) -> None:
    console.print(f"[bold]{info.name}[/]  {styled_status(info.status)}  slot {info.slot}  network {info.network}")
    console.print(f"  directory: {info.directory}")
    if account := info.test_account:
        console.print(f"  test account: {account.username} / {account.password}")
    for name, component in info.components.items():
        console.print(f"\n[bold]{name}[/]  {styled_status(component.status)}")
        for key, value in component.model_dump(exclude={"status"}).items():
            if value is not None:
                console.print(f"  {key}: {value}")


@app.command()
def show(name: NameArg, json_: Annotated[bool, typer.Option("--json", help="Same as `qft --json`")] = False) -> None:
    """Show URLs, ports, credentials, revisions and status of a deployment."""
    if json_:
        set_json_output(True)
    info = Testbed().deployment(name, touch=True).info()
    emit(info, human=lambda: _print_show(info))


@app.command()
def start(name: NameArg, component: ComponentOpt = None, timeout: TimeoutOpt = None) -> None:
    """Materialize and start the components, then wire them together."""
    deployment = Testbed().deployment(name, touch=True)
    deployment.start(component, timeout=_seconds(timeout))
    ok(f"deployment [bold]{name}[/] is running")
    info = deployment.info()
    emit(info, human=lambda: _print_show(info))


@app.command()
def stop(name: NameArg, component: ComponentOpt = None, force: ForceOpt = False) -> None:
    """Stop the components; data and containers are kept for the next start."""
    testbed = Testbed()
    testbed.authorize(name, "stop", force=force)
    deployment = testbed.deployment(name)
    deployment.stop(component)
    ok(f"deployment [bold]{name}[/] stopped")
    emit(deployment.info(), human=lambda: None)


@app.command()
def restart(name: NameArg, component: ComponentOpt = None, timeout: TimeoutOpt = None, force: ForceOpt = False) -> None:
    """Stop then start."""
    testbed = Testbed()
    testbed.authorize(name, "restart", force=force)
    deployment = testbed.deployment(name, touch=True)
    deployment.stop(component)
    deployment.start(component, timeout=_seconds(timeout))
    ok(f"deployment [bold]{name}[/] restarted")
    emit(deployment.info(), human=lambda: None)


@app.command()
def keep(
    name: NameArg,
    ttl: Annotated[str, typer.Option("--ttl", help="New TTL from now, e.g. 8h, 3d, or none to keep it for good")],
    force: ForceOpt = False,
) -> None:
    """Keep a deployment longer: set its TTL, counted from now (`none`: no expiry)."""
    testbed = Testbed()
    testbed.authorize(name, "keep", force=force)
    deployment = testbed.deployment(name)
    testbed.registry.set_ttl(name, parse_ttl(ttl))
    summary = deployment.summary()
    expires = f"until {summary.expires_at.astimezone():%Y-%m-%d %H:%M}" if summary.expires_at else "with no expiry"
    ok(f"deployment [bold]{name}[/] kept {expires}")
    emit(summary, human=lambda: None)


@app.command()
def remove(
    name: NameArg,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
    force: ForceOpt = False,
) -> None:
    """Tear down containers, volumes and files of a deployment and free its slot (the cache is kept)."""
    deployment = Testbed().deployment(name)
    Testbed().authorize(name, "remove", force=force)
    confirmed = yes
    if deployment.summary().session and not confirmed:
        warn(f"deployment '{name}' belongs to bisect session '{deployment.summary().session}'")
    if not confirmed and not confirm(f"Remove deployment '{name}' and all its data?", action=f"remove '{name}'"):
        raise typer.Abort
    deployment.remove()
    ok(f"deployment [bold]{name}[/] removed")
    emit({"name": name, "removed": True}, human=lambda: None)


@app.command()
def logs(
    name: NameArg,
    *,
    component: ComponentOpt = None,
    service: Annotated[
        str | None, typer.Option("--service", help="One compose service, e.g. app or worker_wrapper")
    ] = None,
    tail: Annotated[int | None, typer.Option("--tail", "-n", help="Last N lines per component (default 200)")] = 200,
    since: Annotated[
        str | None, typer.Option("--since", help="Only lines newer than this, e.g. 10m or an ISO date")
    ] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep streaming (not with --json)")] = False,
) -> None:
    """Show container logs of the deployment."""
    if follow and OUTPUT.json:
        raise UsageError("--follow streams forever and cannot answer with one JSON document", hint="drop --follow")
    deployment = Testbed().deployment(name, touch=True)
    captured: dict[str, str] = {}
    for comp in deployment.components(component):
        text = comp.logs(follow=follow, tail=tail, since=since, service=service, capture=OUTPUT.json)
        if text is not None:
            captured[comp.name] = text
    emit({"components": captured}, human=lambda: None)


@app.command()
def launch(
    name: NameArg,
    target: Annotated[str, typer.Argument(help="qgis | android | ios | qfield-desktop", show_default=False)],
) -> None:
    """Open an interactive client of the deployment (QGIS with its profile, the emulator UI, …)."""
    deployment = Testbed().deployment(name)
    launcher = deployment.launcher(target)
    if launcher is None:
        available = (
            sorted(p.name for p in (deployment.directory / "bin").glob("*"))
            if (deployment.directory / "bin").is_dir()
            else []
        )
        raise NotFoundError(
            f"no launcher '{target}' in deployment '{name}'"
            + (f"; available: {', '.join(available)}" if available else " (start it first)")
        )
    qgis = deployment.spec.qgis if target == "qgis" else None
    suffix = f" (Qt platform: {qt_platform(qgis, os.environ)})" if qgis is not None else ""
    console.print(f"launching {launcher}{suffix}")
    # in JSON mode stdout is for the envelope: what the client prints goes to stderr
    completed = subprocess.run([str(launcher)], check=False, stdout=sys.stderr if OUTPUT.json else None)
    emit({"launcher": str(launcher), "exit_code": completed.returncode}, human=lambda: None)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    name: NameArg,
    timeout: Annotated[
        str | None, typer.Option("--timeout", help="Kill the command (and its children) after e.g. 90s, 10m")
    ] = None,
    tail_lines: Annotated[int, typer.Option("--tail-lines", help="Output lines kept in the JSON result")] = 200,
) -> None:
    """Run a command with the deployment's QFT_* environment (URLs, credentials, launchers)."""
    cmd = list(ctx.args)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise UsageError("usage: qft deployment run NAME [--timeout 10m] -- CMD [ARGS...]")
    result = (
        Testbed()
        .deployment(name, touch=True)
        .run(cmd, timeout=_seconds(timeout), capture=OUTPUT.json, tail_lines=tail_lines)
    )
    if result.timed_out:
        raise TimeoutExceededError(
            f"`{shlex.join(cmd)}` did not finish within {timeout}; it was killed",
            hint="raise --timeout, or look at the output tails in details",
            details=asdict(result),
        )
    emit(result, human=lambda: None)
    if not OUTPUT.json:
        # the command's own exit code, as if it had been run directly
        raise typer.Exit(result.exit_code or 0)


@app.command()
def env(
    name: NameArg,
    fmt: Annotated[
        str, typer.Option("--format", "-F", help="shell (`export K='v'`, for eval), dotenv (`K=\"v\"`) or json")
    ] = "shell",
) -> None:
    """Print the QFT_* environment of a deployment, ready for `eval "$(qft deployment env NAME)"`."""
    if fmt not in FORMATS:
        raise UsageError(f"unknown format '{fmt}'", hint="one of: " + ", ".join(FORMATS))
    if fmt == "json":
        set_json_output(True)
    variables = dict(sorted(Testbed().deployment(name, touch=True).env().items()))
    # plain stdout, not rich: the output is meant for `eval` or a file
    emit({"variables": variables}, human=lambda: _write(render(variables, fmt)))


def _write(text: str) -> None:
    sys.stdout.write(text)

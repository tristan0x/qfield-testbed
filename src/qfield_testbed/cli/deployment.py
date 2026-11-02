from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from qfield_testbed.cli._console import (
    OUTPUT,
    confirm,
    console,
    deprecated_force,
    emit,
    ok,
    set_json_output,
    styled_status,
    warn,
)
from qfield_testbed.components.qgis import qt_platform
from qfield_testbed.deployment import DeploymentInfo, DeploymentSummary, Testbed
from qfield_testbed.errors import NotFoundError, UsageError
from qfield_testbed.spec import DeploymentSpec

app = typer.Typer(no_args_is_help=True)

NameArg = Annotated[str, typer.Argument(help="Deployment name", show_default=False)]
ComponentOpt = Annotated[str | None, typer.Option("--component", "-c", help="Act on a single component")]


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
    name: Annotated[str | None, typer.Option("--name", "-n", help="Override the spec's name")] = None,
    set_: Annotated[
        list[str], typer.Option("--set", "-s", help="Override a spec value, e.g. qfieldsync.version=release:v4.22.3")
    ] = [],  # noqa: B006
    start: Annotated[bool, typer.Option("--start", help="Start the deployment right away")] = False,
) -> None:
    """Register a deployment from a spec and allocate its slot (ports, network, directory)."""
    overrides = _parse_overrides(set_)
    if name:
        overrides["name"] = name
    if str(spec_file) == "-":
        spec = DeploymentSpec.from_yaml(typer.get_text_stream("stdin").read(), overrides=overrides)
    else:
        spec = DeploymentSpec.from_file(spec_file, overrides=overrides)
    testbed = Testbed()
    deployment = testbed.add(spec)
    ok(
        f"deployment [bold]{spec.name}[/] registered on slot {deployment.state.slot} "
        f"(ports {deployment.ports.base}–{deployment.ports.base + 99})"
    )
    if start:
        deployment.start()
        ok(f"deployment [bold]{spec.name}[/] is running")
        info = deployment.info()
        emit(info, human=lambda: _print_show(info))
    else:
        emit(deployment.info(), human=lambda: console.print(f"Next: [bold]qft deployment start {spec.name}[/]"))


@app.command("list")
def list_() -> None:
    """List deployments with their slot, status and components."""
    testbed = Testbed()
    deployments = testbed.deployments()
    summaries = [deployment.summary() for deployment in deployments]
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
    table.add_column("SESSION")
    for summary in summaries:
        components = "  ".join(f"{name} {styled_status(status)}" for name, status in summary.components.items())
        table.add_row(
            summary.name,
            str(summary.slot),
            f"{summary.port_base}–{summary.port_last}",
            styled_status(summary.status),
            components,
            summary.session or "",
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
    info = Testbed().deployment(name).info()
    emit(info, human=lambda: _print_show(info))


@app.command()
def start(name: NameArg, component: ComponentOpt = None) -> None:
    """Materialize and start the components, then wire them together."""
    deployment = Testbed().deployment(name)
    deployment.start(component)
    ok(f"deployment [bold]{name}[/] is running")
    info = deployment.info()
    emit(info, human=lambda: _print_show(info))


@app.command()
def stop(name: NameArg, component: ComponentOpt = None) -> None:
    """Stop the components; data and containers are kept for the next start."""
    deployment = Testbed().deployment(name)
    deployment.stop(component)
    ok(f"deployment [bold]{name}[/] stopped")
    emit(deployment.info(), human=lambda: None)


@app.command()
def restart(name: NameArg, component: ComponentOpt = None) -> None:
    """Stop then start."""
    deployment = Testbed().deployment(name)
    deployment.stop(component)
    deployment.start(component)
    ok(f"deployment [bold]{name}[/] restarted")
    emit(deployment.info(), human=lambda: None)


@app.command()
def remove(
    name: NameArg,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Deprecated: use --yes")] = False,
) -> None:
    """Tear down containers, volumes and files of a deployment and free its slot (the cache is kept)."""
    deployment = Testbed().deployment(name)
    if force:
        deprecated_force()
    confirmed = yes or force
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
    component: ComponentOpt = None,
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
) -> None:
    """Show container logs of the deployment."""
    _not_in_json_mode("deployment logs")
    deployment = Testbed().deployment(name)
    for comp in deployment.components(component):
        show_logs = getattr(comp, "logs", None)
        if show_logs is None:
            continue
        show_logs(follow=follow)


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
    completed = subprocess.run([str(launcher)], check=False)
    emit({"launcher": str(launcher), "exit_code": completed.returncode}, human=lambda: None)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(ctx: typer.Context, name: NameArg) -> None:
    """Run a command with the deployment's QFT_* environment (URLs, credentials, launchers)."""
    _not_in_json_mode("deployment run")
    cmd = list(ctx.args)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise UsageError("usage: qft deployment run NAME -- CMD [ARGS...]")
    env = {**os.environ, **Testbed().deployment(name).env()}
    completed = subprocess.run(cmd, env=env, check=False)
    raise typer.Exit(completed.returncode)


@app.command()
def env(name: NameArg) -> None:
    """Print the QFT_* environment of a deployment, ready for `eval "$(qft deployment env NAME)"`."""
    variables = dict(sorted(Testbed().deployment(name).env().items()))
    emit({"variables": variables}, human=lambda: _print_exports(variables))


def _print_exports(variables: dict[str, str]) -> None:
    for key, value in variables.items():
        print(f"export {key}={_shell_quote(value)}")  # noqa: T201 - plain stdout, meant for `eval`


def _not_in_json_mode(command: str) -> None:
    if OUTPUT.json:
        raise UsageError(f"`qft {command}` does not support --json yet", hint="run it without --json")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"

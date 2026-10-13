"""`qft bisect …` — scenario-driven search for the first bad revision of a component."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from qfield_testbed.bisect.algorithm import Verdict
from qfield_testbed.bisect.runner import BisectAbortError, Outcome
from qfield_testbed.bisect.scenario import EXAMPLE_SCENARIO, BisectScenario
from qfield_testbed.bisect.session import BisectSession, SessionStatus
from qfield_testbed.cli import _console
from qfield_testbed.cli._console import confirm, console, ok, print_json, warn
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError

app = typer.Typer(no_args_is_help=True)

SessionArg = Annotated[
    str | None, typer.Argument(help="Session name (the only session when omitted)", show_default=False)
]


def _session(testbed: Testbed, name: str | None) -> BisectSession:
    if name is None:
        names = BisectSession.names(testbed)
        if len(names) != 1:
            raise TestbedError(
                "several sessions exist; name one: " + ", ".join(names)
                if names
                else "no bisect session yet; `qft bisect start SCENARIO.yaml`"
            )
        name = names[0]
    return BisectSession(testbed, name)


def _prompt(message: str) -> str:
    return str(typer.prompt(message.rstrip(": "), default="", show_default=False))


def _print_result(session: BisectSession) -> None:
    info = session.summary()
    if info["first_bad"]:
        fb = info["first_bad"]
        console.print(
            f"\n[bold green]First bad {info['component']} revision:[/] [bold]{fb['label']}[/] — {fb['subject']} ({fb['date']})"
        )
        console.print(f"  {fb['url']}")
        if info["ambiguous"]:
            warn(f"skipped candidates right before it may also be the culprit: {', '.join(info['ambiguous'])}")
    else:
        console.print(
            "\n[bold]No bad candidate isolated[/]: every tested candidate was good."
            + (f" Skipped: {', '.join(info['ambiguous'])}." if info["ambiguous"] else "")
        )
    console.print(f"  {info['tested']} tested, {info['skipped']} skipped — report: {session.directory / 'report.md'}")


@app.command()
def example() -> None:
    """Print an example scenario to start from."""
    console.print(EXAMPLE_SCENARIO, markup=False, highlight=False, end="")


@app.command()
def start(
    scenario_file: Annotated[Path, typer.Argument(help="Scenario (YAML)", show_default=False)],
    session: Annotated[
        str | None, typer.Option("--session", "-s", help="Session name (defaults to the scenario name)")
    ] = None,
    run_: Annotated[bool, typer.Option("--run", help="Run the search right away")] = False,
) -> None:
    """Create a session from a scenario: resolve the candidates and register its deployment."""
    testbed = Testbed()
    scenario = BisectScenario.from_file(scenario_file)
    created = BisectSession.create(testbed, scenario, session)
    ok(
        f"session [bold]{created.name}[/]: {len(created.state.candidates)} candidates between "
        f"{scenario.bisect.good} and {scenario.bisect.bad} (≤ {created.search().remaining_steps()} steps)"
    )
    if run_:
        run(created.name)
    else:
        console.print(f"Next: [bold]qft bisect run {created.name}[/]")


@app.command()
def run(session: SessionArg = None) -> None:
    """Swap, test and record candidates until the first bad one is known (resumes where it stopped)."""
    current = _session(Testbed(), session)
    if current.state.scenario.test.manual and not _console.stdin_is_terminal():
        # checked before the first swap: the prompt would otherwise come after minutes of building and starting
        raise TestbedError(
            "the scenario's test is manual and stdin is not a terminal: run 'qft bisect run' in a terminal, "
            "or use 'qft bisect next' and 'qft bisect good|bad|skip'"
        )
    try:
        while True:
            search = current.search()
            index = search.next_index()
            if index is None:
                break
            candidate = current.candidate(index)
            console.rule(
                f"step {len(current.state.outcomes) + 1} · candidate {index} · {candidate.label} · ≤ {search.remaining_steps()} steps left"
            )
            console.print(f"{candidate.subject}  [dim]{candidate.date}[/]")
            if not current.step(_prompt):
                break
            outcome = current.state.outcomes.get(index)
            if outcome:
                console.print(
                    f"→ [bold]{outcome.verdict}[/]"
                    + (f" (exit {outcome.exit_code})" if outcome.exit_code is not None else "")
                )
    except BisectAbortError as exc:
        warn(str(exc))
        raise typer.Exit(130) from exc
    _print_result(current)


@app.command("next")
def next_(session: SessionArg = None) -> None:
    """Prepare the next candidate in the deployment and wait for `qft bisect good|bad|skip`."""
    current = _session(Testbed(), session)
    if current.state.current is not None:
        candidate = current.candidate(current.state.current)
        console.print(
            f"candidate {current.state.current} ({candidate.label}) is already prepared; give a verdict first"
        )
        return
    index = current.search().next_index()
    if index is None:
        _print_result(current)
        return
    deployment = current.prepare(index)
    if deployment is None:
        console.print("candidate skipped (not materializable); run `qft bisect next` again")
        return
    candidate = current.candidate(index)
    ok(f"candidate {index} ready: [bold]{candidate.label}[/] — {candidate.subject}")
    console.print(f"deployment: {deployment.name}  →  qft deployment show {deployment.name}")
    console.print("then: qft bisect good | bad | skip")


def _verdict(session: str | None, verdict: Verdict) -> None:
    current = _session(Testbed(), session)
    index = current.state.current
    if index is None:
        raise TestbedError("no candidate is prepared; run `qft bisect next` first")
    current.record(index, Outcome(verdict, None, 0.0, f"manual verdict: {verdict}\n"))
    ok(f"candidate {index} ({current.candidate(index).label}) → {verdict}")
    if current.state.status is SessionStatus.DONE:
        _print_result(current)
    else:
        console.print(f"≤ {current.search().remaining_steps()} steps left — qft bisect next")


@app.command()
def good(session: SessionArg = None) -> None:
    """Record the prepared candidate as good."""
    _verdict(session, Verdict.GOOD)


@app.command()
def bad(session: SessionArg = None) -> None:
    """Record the prepared candidate as bad."""
    _verdict(session, Verdict.BAD)


@app.command()
def skip(session: SessionArg = None) -> None:
    """Record the prepared candidate as untestable."""
    _verdict(session, Verdict.SKIP)


@app.command()
def status(session: SessionArg = None, json_: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show candidates, verdicts and what comes next."""
    current = _session(Testbed(), session)
    info = current.summary()
    if json_:
        print_json(info)
        return
    console.print(
        f"[bold]{info['session']}[/]  {info['status']}  {info['component']} {info['good']}..{info['bad']}  "
        f"deployment {info['deployment']}  {info['tested']} tested / {info['skipped']} skipped / {info['candidates']} candidates"
    )
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("CANDIDATE")
    table.add_column("SUBJECT")
    table.add_column("VERDICT")
    for row in info["verdicts"]:
        marker = ""
        if row["index"] == info["current"]:
            marker = "[cyan]prepared[/]"
        elif row["index"] == info["next"]:
            marker = "[dim]next[/]"
        verdict = {"good": "[green]good[/]", "bad": "[red]bad[/]", "skip": "[yellow]skip[/]"}.get(
            row["verdict"] or "", marker
        )
        table.add_row(str(row["index"]), row["label"], row["subject"][:70], verdict)
    console.print(table)
    if info["status"] == SessionStatus.DONE:
        _print_result(current)


@app.command("list")
def list_() -> None:
    """List bisect sessions."""
    testbed = Testbed()
    names = BisectSession.names(testbed)
    if not names:
        console.print("No bisect session yet. Try: [bold]qft bisect example > scenario.yaml[/]")
        return
    table = Table(box=None, header_style="bold", pad_edge=False)
    for column in ("SESSION", "STATUS", "COMPONENT", "RANGE", "PROGRESS", "DEPLOYMENT"):
        table.add_column(column)
    for name in names:
        info = BisectSession(testbed, name).summary()
        table.add_row(
            name,
            info["status"],
            info["component"],
            f"{info['good']}..{info['bad']}",
            f"{info['tested'] + info['skipped']}/{info['candidates']}",
            info["deployment"],
        )
    console.print(table)


@app.command()
def log(
    session: SessionArg = None,
    index: Annotated[
        int | None, typer.Option("--index", "-i", help="One candidate's log instead of the summary")
    ] = None,
) -> None:
    """Show the test output recorded for each step."""
    current = _session(Testbed(), session)
    if index is not None:
        record = current.state.outcomes.get(index)
        if record is None or not record.log_file:
            raise TestbedError(f"no log for candidate {index}")
        console.print(Path(record.log_file).read_text(), markup=False, highlight=False)
        return
    for i, record in sorted(current.state.outcomes.items()):
        candidate = current.candidate(i)
        console.print(
            f"[bold]{i}[/] {candidate.label} → {record.verdict} ({record.duration_s}s) {record.log_file or ''}"
        )


@app.command()
def reset(
    session: SessionArg = None,
    remove_deployment: Annotated[
        bool, typer.Option("--remove-deployment", help="Also tear down the session's deployment")
    ] = False,
) -> None:
    """Forget all verdicts and start over (candidates are kept)."""
    current = _session(Testbed(), session)
    current.reset(remove_deployment=remove_deployment)
    ok(f"session [bold]{current.name}[/] reset")


@app.command()
def remove(
    session: Annotated[str, typer.Argument(show_default=False)],
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
) -> None:
    """Delete a session and its deployment."""
    current = BisectSession(Testbed(), session)
    question = f"Remove session '{session}' and deployment '{current.state.deployment_name}'?"
    if not force and not confirm(question, action=f"remove session '{session}'"):
        raise typer.Abort
    current.remove()
    ok(f"session [bold]{session}[/] removed")

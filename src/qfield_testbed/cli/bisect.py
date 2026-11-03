"""`qft bisect …` — scenario-driven search for the first bad revision of a component."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from qfield_testbed import service
from qfield_testbed.bisect.algorithm import Verdict
from qfield_testbed.bisect.scenario import EXAMPLE_SCENARIO, BisectScenario
from qfield_testbed.bisect.session import BisectSession, SessionStatus
from qfield_testbed.cli import _console
from qfield_testbed.cli._console import confirm, console, emit, ok, set_json_output, warn
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import NotFoundError

app = typer.Typer(no_args_is_help=True)

SessionArg = Annotated[
    str | None, typer.Argument(help="Session name (the only session when omitted)", show_default=False)
]


def _session(testbed: Testbed, name: str | None) -> BisectSession:
    return service.bisect_session(name, testbed)


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
    emit(
        {"yaml": EXAMPLE_SCENARIO}, human=lambda: console.print(EXAMPLE_SCENARIO, markup=False, highlight=False, end="")
    )


@app.command()
def start(
    scenario_file: Annotated[Path, typer.Argument(help="Scenario (YAML)", show_default=False)],
    session: Annotated[
        str | None, typer.Option("--session", "-s", help="Session name (defaults to the scenario name)")
    ] = None,
    run_: Annotated[bool, typer.Option("--run", help="Run the search right away")] = False,
) -> None:
    """Create a session from a scenario: resolve the candidates and register its deployment."""
    scenario = BisectScenario.from_file(scenario_file)
    created = service.bisect_start(scenario, session=session)
    ok(
        f"session [bold]{created.name}[/]: {len(created.state.candidates)} candidates between "
        f"{scenario.bisect.good} and {scenario.bisect.bad} (≤ {created.search().remaining_steps()} steps)"
    )
    if run_:
        run(created.name)
    else:
        emit(
            service.bisect_summary(created),
            human=lambda: console.print(f"Next: [bold]qft bisect run {created.name}[/]"),
        )


@app.command()
def run(session: SessionArg = None) -> None:
    """Swap, test and record candidates until the first bad one is known (resumes where it stopped)."""
    current = _session(Testbed(), session)

    def before(current: BisectSession, index: int) -> None:
        candidate = current.candidate(index)
        console.rule(
            f"step {len(current.state.outcomes) + 1} · candidate {index} · {candidate.label} · "
            f"≤ {current.search().remaining_steps()} steps left"
        )
        console.print(f"{candidate.subject}  [dim]{candidate.date}[/]")

    def after(current: BisectSession, index: int) -> None:
        outcome = current.state.outcomes.get(index)
        if outcome:
            console.print(
                f"→ [bold]{outcome.verdict}[/]"
                + (f" (exit {outcome.exit_code})" if outcome.exit_code is not None else "")
            )

    prompt = _prompt if _console.is_interactive() else None
    summary = service.bisect_run(current, prompt=prompt, before=before, after=after)
    emit(summary, human=lambda: _print_result(current))


@app.command("next")
def next_(session: SessionArg = None) -> None:
    """Prepare the next candidate in the deployment and wait for `qft bisect good|bad|skip`."""
    current = _session(Testbed(), session)
    result = service.bisect_next(current)
    if result.outcome == "already-prepared":
        candidate = current.candidate(current.state.current or 0)
        message = f"candidate {result.prepared} ({candidate.label}) is already prepared; give a verdict first"
        emit(result, human=lambda: console.print(message))
        return
    if result.outcome == "done":
        emit(result, human=lambda: _print_result(current))
        return
    if result.outcome == "skipped":
        emit(result, human=lambda: console.print("candidate skipped (not materializable); run `qft bisect next` again"))
        return
    candidate = current.candidate(result.prepared or 0)
    ok(f"candidate {result.prepared} ready: [bold]{candidate.label}[/] — {candidate.subject}")

    def human() -> None:
        console.print(
            f"deployment: {current.state.deployment_name}  →  qft deployment show {current.state.deployment_name}"
        )
        console.print("then: qft bisect good | bad | skip")

    emit(result, human=human)


def _verdict(session: str | None, verdict: Verdict) -> None:
    current = _session(Testbed(), session)
    index = service.bisect_verdict(current, verdict)
    ok(f"candidate {index} ({current.candidate(index).label}) → {verdict}")

    def human() -> None:
        if current.state.status is SessionStatus.DONE:
            _print_result(current)
        else:
            console.print(f"≤ {current.search().remaining_steps()} steps left — qft bisect next")

    emit(service.bisect_summary(current), human=human)


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
def status(
    session: SessionArg = None, json_: Annotated[bool, typer.Option("--json", help="Same as `qft --json`")] = False
) -> None:
    """Show candidates, verdicts and what comes next."""
    if json_:
        set_json_output(True)
    current = _session(Testbed(), session)
    info = current.summary()
    emit(service.bisect_summary(current), human=lambda: _print_status(current, info))


def _print_status(current: BisectSession, info: dict[str, Any]) -> None:
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
    sessions = [BisectSession(testbed, name).summary() for name in BisectSession.names(testbed)]
    emit({"sessions": sessions}, human=lambda: _print_sessions(sessions))


def _print_sessions(sessions: list[dict[str, Any]]) -> None:
    if not sessions:
        console.print("No bisect session yet. Try: [bold]qft bisect example > scenario.yaml[/]")
        return
    table = Table(box=None, header_style="bold", pad_edge=False)
    for column in ("SESSION", "STATUS", "COMPONENT", "RANGE", "PROGRESS", "DEPLOYMENT"):
        table.add_column(column)
    for info in sessions:
        table.add_row(
            info["session"],
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
            raise NotFoundError(f"no log for candidate {index}")
        text = Path(record.log_file).read_text()
        emit({"index": index, "log": text}, human=lambda: console.print(text, markup=False, highlight=False))
        return
    outcomes = [
        {
            "index": i,
            "label": current.candidate(i).label,
            "verdict": record.verdict,
            "duration_s": record.duration_s,
            "log_file": record.log_file,
        }
        for i, record in sorted(current.state.outcomes.items())
    ]

    def human() -> None:
        for row in outcomes:
            console.print(
                f"[bold]{row['index']}[/] {row['label']} → {row['verdict']} ({row['duration_s']}s) {row['log_file'] or ''}"
            )

    emit({"outcomes": outcomes}, human=human)


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
    emit(current.summary(), human=lambda: None)


@app.command()
def remove(
    session: Annotated[str, typer.Argument(show_default=False)],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="From an agent session: remove it although it owns nothing here")
    ] = False,
) -> None:
    """Delete a session and its deployment."""
    testbed = Testbed()
    current = BisectSession(testbed, session)
    testbed.authorize(current.state.deployment_name, "remove", force=force)
    question = f"Remove session '{session}' and deployment '{current.state.deployment_name}'?"
    if not yes and not confirm(question, action=f"remove session '{session}'"):
        raise typer.Abort
    current.remove()
    ok(f"session [bold]{session}[/] removed")
    emit({"session": session, "removed": True}, human=lambda: None)

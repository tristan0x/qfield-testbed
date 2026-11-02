"""`qft docs` — the documentation of the installed version, offline."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.markdown import Markdown

from qfield_testbed.cli._console import console, emit
from qfield_testbed.docs import Topic, read, topics


def docs(
    topic: Annotated[
        str | None, typer.Argument(help="Topic, e.g. spec, glossary, components/qgis", show_default=False)
    ] = None,
    list_: Annotated[bool, typer.Option("--list", "-l", help="List the topics")] = False,
) -> None:
    """Show the documentation matching this version of qft (the topics without argument)."""
    if topic is None or list_:
        available = topics()
        emit({"topics": available}, human=lambda: _print_topics(available))
        return
    page, markdown = read(topic)
    emit(
        {"topic": page.topic, "title": page.title, "markdown": markdown},
        human=lambda: console.print(Markdown(markdown)) if sys.stdout.isatty() else sys.stdout.write(markdown),
    )


def _print_topics(available: list[Topic]) -> None:
    width = max(len(t.topic) for t in available)
    for entry in available:
        console.print(f"{entry.topic:<{width}}  {entry.title}", markup=False, highlight=False)

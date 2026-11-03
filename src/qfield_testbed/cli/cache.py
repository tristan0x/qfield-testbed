from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from qfield_testbed import service
from qfield_testbed.cache import human_size
from qfield_testbed.cli._console import console, emit, ok
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes import docker

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_() -> None:
    """List cached materializations, their size and the deployments using them."""
    data = service.cache_list()
    emit(data, human=lambda: _print_entries(data))


def _print_entries(data: service.CacheList) -> None:
    if not data.entries:
        console.print(f"Cache is empty ({data.cache_dir})")
        return
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("KEY")
    table.add_column("SIZE", justify="right")
    table.add_column("LAST USED")
    table.add_column("USED BY")
    table.add_column("IMAGES")
    for entry in data.entries:
        table.add_row(
            entry.key,
            human_size(entry.size_bytes),
            entry.last_used.strftime("%Y-%m-%d"),
            ", ".join(entry.users),
            ", ".join(entry.images),
        )
    console.print(table)
    console.print(f"\n{len(data.entries)} entries, {human_size(data.total_bytes)} on disk in {data.cache_dir}")


@app.command()
def prune(
    older_than: Annotated[str | None, typer.Option("--older-than", help="e.g. 30d (default from config)")] = None,
    docker_build_cache: Annotated[
        bool, typer.Option("--docker-build-cache", help="Also `docker builder prune`")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Remove cache entries no deployment references and that were not used recently."""
    pruned = service.cache_prune(older_than=older_than, docker_build_cache=docker_build_cache, dry_run=dry_run)
    if not pruned.removed:
        console.print("Nothing to prune.")
    for entry in pruned.removed:
        size = human_size(entry.size_bytes) if entry.size_bytes is not None else "gone"
        console.print(f"{'would remove' if dry_run else 'removing'} {entry.key} ({size})")
    if not dry_run:
        ok("cache pruned")
    _report_dangling_images(Testbed())
    emit(pruned, human=lambda: None)


def _report_dangling_images(testbed: Testbed) -> None:
    images = docker.list_images(testbed.runner, "qft-*")
    if images:
        console.print(f"[dim]{len(images)} qft-* images present (shared by revision; removed with their entries)[/]")

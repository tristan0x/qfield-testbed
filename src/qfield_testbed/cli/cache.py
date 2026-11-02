from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any

import typer
from rich.table import Table

from qfield_testbed.cache import dir_size, human_size
from qfield_testbed.cli._console import console, emit, ok
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import UsageError
from qfield_testbed.runtimes import docker

app = typer.Typer(no_args_is_help=True)

_DURATION = re.compile(r"^(\d+)([dhm])$")
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


def parse_duration(value: str) -> timedelta:
    match = _DURATION.match(value)
    if not match:
        raise UsageError(f"invalid duration '{value}'; use e.g. 30d, 12h, 90m")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


@app.command("list")
def list_() -> None:
    """List cached materializations, their size and the deployments using them."""
    testbed = Testbed()
    index = testbed.cache.load()
    entries: list[dict[str, Any]] = [
        {
            "key": key,
            "path": entry.path,
            "size_bytes": dir_size(entry.path) if entry.path.exists() else 0,
            "last_used": entry.last_used,
            "users": sorted(entry.users),
            "images": entry.images,
        }
        for key, entry in sorted(index.entries.items())
    ]
    total = sum(e["size_bytes"] for e in entries)
    data = {"cache_dir": testbed.paths.cache, "entries": entries, "total_bytes": total}
    emit(data, human=lambda: _print_entries(testbed, entries, total))


def _print_entries(testbed: Testbed, entries: list[dict[str, Any]], total: int) -> None:
    if not entries:
        console.print(f"Cache is empty ({testbed.paths.cache})")
        return
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("KEY")
    table.add_column("SIZE", justify="right")
    table.add_column("LAST USED")
    table.add_column("USED BY")
    table.add_column("IMAGES")
    for entry in entries:
        table.add_row(
            entry["key"],
            human_size(entry["size_bytes"]),
            entry["last_used"].strftime("%Y-%m-%d"),
            ", ".join(entry["users"]),
            ", ".join(entry["images"]),
        )
    console.print(table)
    console.print(f"\n{len(entries)} entries, {human_size(total)} on disk in {testbed.paths.cache}")


@app.command()
def prune(
    older_than: Annotated[str | None, typer.Option("--older-than", help="e.g. 30d (default from config)")] = None,
    docker_build_cache: Annotated[
        bool, typer.Option("--docker-build-cache", help="Also `docker builder prune`")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Remove cache entries no deployment references and that were not used recently."""
    testbed = Testbed()
    age = parse_duration(older_than) if older_than else timedelta(days=testbed.config.cache.prune_after_days)
    live = set(testbed.registry.names())
    candidates = testbed.cache.prunable(older_than=age, live_users=live)
    if not candidates:
        console.print("Nothing to prune.")
    removed: list[dict[str, Any]] = []
    for key, entry in sorted(candidates.items()):
        size_bytes = dir_size(entry.path) if entry.path.exists() else None
        removed.append({"key": key, "size_bytes": size_bytes})
        size = human_size(size_bytes) if size_bytes is not None else "gone"
        console.print(f"{'would remove' if dry_run else 'removing'} {key} ({size})")
        if not dry_run:
            testbed.cache.drop(key)
            for image in entry.images:
                testbed.runner.run(["docker", "image", "rm", image], check=False)
    if docker_build_cache and not dry_run:
        testbed.runner.run(["docker", "builder", "prune", "-f"], check=False, capture=False)
    if not dry_run:
        ok("cache pruned")
    _report_dangling_images(testbed)
    emit({"dry_run": dry_run, "removed": removed}, human=lambda: None)


def _report_dangling_images(testbed: Testbed) -> None:
    images = docker.list_images(testbed.runner, "qft-*")
    if images:
        console.print(f"[dim]{len(images)} qft-* images present (shared by revision; removed with their entries)[/]")

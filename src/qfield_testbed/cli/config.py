from __future__ import annotations

import typer
import yaml

from qfield_testbed.cli._console import console, emit
from qfield_testbed.config import GlobalConfig

app = typer.Typer(no_args_is_help=True)


@app.command()
def show() -> None:
    """Print the effective configuration (file values, defaults and environment overrides)."""
    config = GlobalConfig.load()
    emit(
        config,
        human=lambda: console.print(
            yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), markup=False, highlight=False
        ),
    )


@app.command()
def path() -> None:
    """Print the configuration file path."""
    file = GlobalConfig.file_path()
    exists = file.is_file()
    emit(
        {"path": file, "exists": exists},
        human=lambda: console.print(f"{file}{'' if exists else '  (not created yet; defaults apply)'}", markup=False),
    )

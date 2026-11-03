# Working on qfield-testbed

This file is for coding agents working **on** this repository. To *use* the testbed from another project, see
[docs/agents.md](docs/agents.md).

## Layout

- `src/qfield_testbed/`: the `qft` CLI. The core (`deployment.py`: `Testbed`, `Deployment`; `bisect/`) is separate
  from the CLI adapters (`cli/`). `CONTRIBUTING.md` maps the modules.
- `docs/`: user documentation, shipped in the wheel and printed by `qft docs TOPIC`. `docs/glossary.md` is
  authoritative for vocabulary.
- `plugin/` and `.claude-plugin/marketplace.json`: the Claude Code plugin (skill, sub-agent, SessionStart hook).
- `tests/unit/` (no Docker) and `tests/integration/` (`-m docker`).

## Commands

```sh
uv sync --group dev
uv run pre-commit run -a     # ruff (all rules), ruff format, mypy --strict, pyright, vulture, deptry
uv run pytest --cov          # unit tests, coverage must stay >= 85 %
QFT_INTEGRATION=1 uv run pytest -m docker -o addopts=""   # integration tests, pull images and start containers
claude plugin validate . --strict && claude plugin validate ./plugin --strict   # after changing plugin/
```

## Rules

- Failures raise the `TestbedError` subclass that says what went wrong (`errors.py`), never `TestbedError` itself;
  the next thing to try goes in `hint=`. The class fixes the exit code and the `--json` error code
  ([docs/adr/0001-agent-interface.md](docs/adr/0001-agent-interface.md)).
- With `--json`, stdout carries exactly one envelope: anything else a command or a subprocess prints goes to
  stderr. Every command returns its data through `emit()` (`cli/_console.py`); `docs/json-output.md` lists it.
- Use the glossary's terms in code, CLI and docs; a new concept adds a glossary row in the same change.
- A change that touches processes, Docker or the CLI output is checked for real before merging (a deployment with
  `qgis: host` and a QFieldCloud release, run with `--json`): unit tests with `FakeRunner` have missed bugs that
  only showed end to end.
- Commands quoted in `plugin/`, `docs/`, the README and `llms.txt` must exist in the CLI: `tests/unit/test_drift.py`
  checks every `qft …` line and every `qft docs TOPIC`, and keeps the skill within its size budget.
- Commits follow Conventional Commits; every user-facing change gets a line under *Unreleased* in `CHANGELOG.md`.

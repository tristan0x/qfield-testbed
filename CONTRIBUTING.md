# Contributing

## Setup

```sh
uv sync --group dev
uv run pre-commit install
uv run pytest                       # unit tests, no Docker needed
QFT_INTEGRATION=1 uv run pytest -m docker -o addopts=""   # integration tests (pull images, start containers)
```

`ruff` (lint + format) and `mypy --strict` run through pre-commit and in CI.

## Vocabulary

[docs/glossary.md](docs/glossary.md) is authoritative. Module names, classes, CLI nouns and verbs, log messages
and documentation use its terms and no synonyms. A change that introduces a new concept adds it to the glossary
in the same pull request.

## Commit messages

Conventional-Commits subjects: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`. The subject is short and
synthetic, in the imperative mood. The body — when the subject is not enough — explains what the change does for
the user and why, not how it is implemented. Messages do not mention tooling used to write the code and carry
no attribution trailers.

```
feat: add the qfield-android component

QField release APKs install on a budtmo/docker-android emulator that joins the
deployment network; the app is pre-configured with the deployment's QFieldCloud URL.
```

## Branches and releases

Work happens on branches merged into `master` through pull requests. Releases are tags `vX.Y.Z`; every
user-facing change gets a line in [CHANGELOG.md](CHANGELOG.md) under *Unreleased*.

## Layout

```
src/qfield_testbed/
  spec.py            deployment spec and version sources
  state.py           registry (slots) and per-deployment state
  ports.py           port blocks
  cache.py           shared cache index
  deployment.py      orchestrator (Testbed, Deployment)
  components/        one module per component (lifecycle in base.py)
  backends/          runtime strategies shared by components
  provision/         wiring: accounts, profiles, app settings
  revisions/         git / GitHub resolution of version sources
  runtimes/          shell, docker, adb, ssh, http wrappers
  bisect/            scenario, algorithm, session, runner, report
  cli/               typer commands
  assets/            templates and the sample project
```

Components never call `subprocess` directly: they go through the injected `Runner`, so unit tests use
`FakeRunner` to assert the commands a component would run.

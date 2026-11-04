"""The skill, the docs and the CLI stay in step: every `qft …` they quote exists, with the options it uses.

Command lines are taken from fenced code blocks and inline code of the Markdown an agent or a person reads. Words
the CLI cannot check — placeholders (`NAME`, `<tag>`, `$VAR`, `…`) and positional arguments — are skipped; command
names and options are not.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.main import get_command

from qfield_testbed.cli import app
from qfield_testbed.docs import read
from qfield_testbed.errors import NotFoundError

REPO = Path(__file__).parents[2]
SKILL = REPO / "plugin" / "skills" / "qfield-testbed"
SOURCES = [
    *sorted((REPO / "plugin").rglob("*.md")),
    *sorted((REPO / "docs").rglob("*.md")),
    *sorted((REPO / "examples").rglob("*.md")),
    REPO / "README.md",
    REPO / "AGENTS.md",
    REPO / "llms.txt",
]
_QFT = re.compile(r"(?<![\w./-])qft(?=\s+[-\w])")
_INLINE = re.compile(r"`([^`]+)`")
# commands an ADR names on purpose although they do not exist (yet): (file, command line)
NOT_COMMANDS = {
    ("docs/adr/0001-agent-interface.md", "qft session"),  # rejected by the ADR
}
_STOP = {"--", "#", "|", "||", "&&", ";", ">", "<", "2>&1", "&"}


@dataclass
class Quote:
    path: Path
    line: int
    text: str  # from `qft` on

    def __str__(self) -> str:
        return f"{self.path.relative_to(REPO)}:{self.line}: {self.text}"


def quotes(path: Path) -> Iterator[Quote]:
    fenced = False
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        # a fenced Markdown example still marks its commands as inline code
        spans = [line] if fenced and "`" not in line else _INLINE.findall(line)
        for span in spans:
            text = span.replace("\\|", "|")
            for match in _QFT.finditer(text):
                yield Quote(path, number, text[match.start() :])


def _placeholder(token: str) -> bool:
    bare = token.strip("[]")
    return (
        not bare
        or bool(re.fullmatch(r"[A-Z][A-Z0-9_]*(\|[A-Z][A-Z0-9_]*)*", bare))
        or any(mark in bare for mark in ("<", "$", "…", "...", "{", "*", "'", '"'))
    )


def _tokens(text: str) -> Iterator[str]:
    try:  # shell quoting, when the quote holds the whole command line
        raws = shlex.split(text, comments=True)[1:]
        closed = False
    except ValueError:  # the command sits inside a quote or a `$(…)` that started before `qft`
        raws = text.split()[1:]
        closed = True
    for raw in raws:
        if raw in _STOP:
            return
        token = raw.rstrip(")\"'`,.:") if closed else raw.rstrip(")`,.:")
        if token:
            yield token
        if token != raw and (closed or raw.endswith(")")):  # the closing quote or parenthesis ends the command
            return


def _options(command: Any) -> dict[str, Any]:
    return {opt: param for param in command.params for opt in (*param.opts, *param.secondary_opts)}


def _takes_value(param: Any) -> bool:
    return param.param_type_name == "option" and not param.is_flag and not param.count


def check(quote: Quote, root: Any) -> list[str]:
    """What is wrong with the command line of ``quote``: unknown commands and options, missing docs topics."""
    problems: list[str] = []
    command: Any = root
    path = ["qft"]
    tokens = list(_tokens(quote.text))
    skip_value = False
    positional: list[str] = []
    for token in tokens:
        bare = token.strip("[]")
        if skip_value:
            skip_value = False
            continue
        if bare.startswith("-") and bare not in {"-", "--"}:
            for alternative in bare.split("|"):
                name = alternative.split("=", 1)[0]
                known = {**_options(root), **_options(command)}
                if name not in known:
                    problems.append(f"{quote} — unknown option {name} for `{' '.join(path)}`")
                    continue
                skip_value = _takes_value(known[name]) and "=" not in alternative
            continue
        if hasattr(command, "commands"):
            if _placeholder(token):
                break
            names = bare.split("|")
            unknown = [name for name in names if name not in command.commands]
            if unknown:
                problems.append(f"{quote} — unknown command `{' '.join([*path, *unknown])}`")
                break
            if len(names) > 1:
                break
            command = command.commands[names[0]]
            path.append(names[0])
            continue
        positional.append(token)
    if path == ["qft", "docs"] and positional and not _placeholder(positional[0]):
        try:
            read(positional[0])
        except NotFoundError:
            problems.append(f"{quote} — no documentation topic `{positional[0]}`")
    return problems


def _quote(text: str) -> Quote:
    """``text`` quoted on a line of the README, from `qft` on as `quotes` does."""
    match = _QFT.search(text)
    assert match is not None
    return Quote(REPO / "README.md", 1, text[match.start() :])


def _all_quotes() -> list[Quote]:
    return [
        quote
        for path in SOURCES
        for quote in quotes(path)
        if (path.relative_to(REPO).as_posix(), quote.text) not in NOT_COMMANDS
    ]


def test_sources_quote_commands() -> None:
    found = _all_quotes()
    assert len(found) > 100
    assert {quote.path for quote in found} >= {SKILL / "SKILL.md", REPO / "README.md"}


def test_every_quoted_command_exists() -> None:
    root = get_command(app)
    problems = [problem for quote in _all_quotes() for problem in check(quote, root)]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("qft --json deployment ensure SPEC --timout 20m", "unknown option --timout"),
        ("qft deployment ensur SPEC", "unknown command `qft deployment ensur`"),
        ("qft deployment list|shw NAME", "unknown command `qft deployment shw`"),
        ("qft docs specification", "no documentation topic `specification`"),
        ("qft gc [--expird] [--dry-run]", "unknown option --expird"),
        ('qft deployment ensure "$TMPDIR/spec.yaml" --wait 20m', "unknown option --wait"),
        ('eval "$(qft deployment envv NAME)"', "unknown command `qft deployment envv`"),
    ],
)
def test_the_check_catches_drift(text: str, problem: str) -> None:
    root = get_command(app)
    (found,) = check(_quote(text), root)
    assert problem in found


@pytest.mark.parametrize(
    "text",
    [
        "qft --json --agent-session ID deployment run NAME --timeout 10m -- CMD --not-an-option",
        "qft deployment logs NAME --tail 100 [--component C] [--service worker_wrapper]",
        "qft deployment env NAME --format shell|dotenv|json",
        "qft docs components/qgis",
        'eval "$(qft deployment env NAME)"',
        "qft bisect example|start|run",
        'qft --json spec suggest --path . --output "$TMPDIR/qft-spec.yaml"',
    ],
)
def test_valid_lines_pass(text: str) -> None:
    root = get_command(app)
    assert check(_quote(text), root) == []


def _frontmatter(path: Path) -> dict[str, Any]:
    _, header, _ = path.read_text().split("---", 2)
    data = yaml.safe_load(header)
    assert isinstance(data, dict)
    return data


def test_the_skill_stays_small() -> None:
    skill = SKILL / "SKILL.md"
    assert len(skill.read_text().splitlines()) <= 200
    header = _frontmatter(skill)
    assert len(header["description"]) + len(header.get("when_to_use", "")) <= 1536
    for reference in (SKILL / "references").glob("*.md"):
        assert len(reference.read_text().splitlines()) <= 300, reference
    for link in re.findall(r"\]\((references/[^)]+)\)", skill.read_text()):
        assert (SKILL / link).is_file(), link


def _words(text: str) -> str:
    return " ".join(line.removeprefix(">").strip() for line in text.splitlines()).replace("  ", " ")


def test_the_examples_quote_the_eval_prompts() -> None:
    examples = sorted((REPO / "examples" / "agents").glob("uc*.md"))
    assert len(examples) == 8
    for example in examples:
        number = example.name.split("-", 1)[0]
        cases = sorted((REPO / "evals" / "cases").glob(f"{number}-*.yaml"))
        prompts = [" ".join(yaml.safe_load(case.read_text())["prompt"].split()) for case in cases]
        assert any(prompt in " ".join(_words(example.read_text()).split()) for prompt in prompts), example

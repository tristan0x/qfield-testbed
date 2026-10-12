"""The bisection itself, independent of deployments: candidates in order, verdicts, next pick, result.

Candidates are ordered oldest first. Index ``-1`` is the known-good boundary, ``len(candidates)`` the
known-bad one. The first bad candidate is the lowest index with a bad verdict once every candidate before it is
good or skipped; skipped neighbours make the answer ambiguous, exactly as in ``git bisect``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Verdict(StrEnum):
    GOOD = "good"
    BAD = "bad"
    SKIP = "skip"

    @classmethod
    def from_exit_code(cls, code: int) -> Verdict:
        """``git bisect run`` semantics: 0 good, 125 skip, 1-127 bad; 128+ is handled by the caller as abort."""
        if code == 0:
            return cls.GOOD
        if code == 125:  # noqa: PLR2004
            return cls.SKIP
        return cls.BAD


ABORT_EXIT_CODE = 128


@dataclass
class Result:
    first_bad: int | None
    """Index of the first bad candidate, or None when no bad candidate could be isolated."""

    ambiguous: list[int] = field(default_factory=list)
    """Skipped candidates right before the first bad one: the regression may hide in any of them."""

    tested: int = 0
    skipped: int = 0


@dataclass
class Search:
    size: int
    verdicts: dict[int, Verdict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.size < 1:
            raise ValueError("a bisect needs at least one candidate between good and bad")

    # --- bounds -----------------------------------------------------------------------------------------

    @property
    def last_good(self) -> int:
        """Highest index known good (-1 = the `good` boundary)."""
        goods = [i for i, v in self.verdicts.items() if v is Verdict.GOOD]
        return max(goods, default=-1)

    @property
    def first_known_bad(self) -> int:
        """Lowest index known bad (size = the `bad` boundary)."""
        bads = [i for i, v in self.verdicts.items() if v is Verdict.BAD]
        return min(bads, default=self.size)

    def untested_between(self) -> list[int]:
        return [i for i in range(self.last_good + 1, self.first_known_bad) if i not in self.verdicts]

    # --- steps ------------------------------------------------------------------------------------------

    def next_index(self) -> int | None:
        """The candidate to test next: the middle of the open span, preferring untested ones; None when done."""
        span = self.untested_between()
        if not span:
            return None
        middle = (self.last_good + self.first_known_bad) / 2
        # closest untested to the middle; ties go to the earlier candidate (deterministic resume)
        return min(span, key=lambda i: (abs(i - middle), i))

    def record(self, index: int, verdict: Verdict) -> None:
        if not 0 <= index < self.size:
            raise IndexError(index)
        self.verdicts[index] = verdict

    @property
    def done(self) -> bool:
        return self.next_index() is None

    def remaining_steps(self) -> int:
        """Upper bound on the tests still needed (log2 of the open span)."""
        span = len(self.untested_between())
        return span.bit_length()

    def result(self) -> Result:
        tested = sum(1 for v in self.verdicts.values() if v is not Verdict.SKIP)
        skipped = sum(1 for v in self.verdicts.values() if v is Verdict.SKIP)
        first_bad = self.first_known_bad
        if first_bad >= self.size:
            # every candidate between good and bad was good or skipped: the `bad` ref itself is the first bad
            # only if nothing was skipped right before it; report None otherwise
            ambiguous = self._skipped_before(self.size)
            return Result(first_bad=None, ambiguous=ambiguous, tested=tested, skipped=skipped)
        return Result(first_bad=first_bad, ambiguous=self._skipped_before(first_bad), tested=tested, skipped=skipped)

    def _skipped_before(self, index: int) -> list[int]:
        out: list[int] = []
        i = index - 1
        while i >= 0 and self.verdicts.get(i) is Verdict.SKIP:
            out.append(i)
            i -= 1
        return sorted(out)

# UC5: which release changed a behaviour

**Context**: any checkout of the stack; a host with Docker. The bisect pulls one QFieldCloud release per step.

**Prompt**

> Find the first QFieldCloud release between v26.25 and v26.29 whose /api/v1/status/ answer differs from v26.25's
> in its set of top-level keys (if none does, say so). Do it automatically with official releases, report the
> result, and clean up.

**What happens**

1. The agent writes a test script (exit 0 when the keys match v26.25's) and a scenario with `good: v26.25` and
   `bad: v26.29`, then checks it with `qft --json spec validate --scenario FILE`.
2. `qft --json bisect start FILE` and `qft --json bisect run NAME`: each step swaps the QFieldCloud release and runs
   the script.
3. `qft --json bisect remove NAME --yes` removes the bisect and its deployment.

**How to check**: the report names the first release that differs (v26.29 adds the `version` key) and the
steps it tested.

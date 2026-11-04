# UC8: a host that cannot run the stack

**Context**: a QFieldSync checkout on a host without Docker.

**Prompt**

> Check that this QFieldSync checkout can still package a project against QFieldCloud master, end to end.

**What happens**

1. `qft --json doctor` answers that Docker is missing, with the hint to install it.
2. The agent stops there and asks the human, instead of working around it: nothing is created.

**How to check**: the report says what is missing and what the human can do, and `qft deployment list` is
unchanged.

# Pointer for upstream projects

QFieldCloud, QFieldSync, QField or a QGIS plugin can tell coding agents about the testbed by adding these lines to
their own `AGENTS.md` or `CLAUDE.md`. They are optional: the Claude Code plugin finds these checkouts by itself.

```markdown
## Testing against a running QField stack

To reproduce a bug or test a change against a real QFieldCloud, QGIS + QFieldSync or QField, use
[qfield-testbed](https://github.com/tristan0x/qfield-testbed): `qft --json spec suggest --path .` proposes a
deployment running this checkout live, `qft --json deployment ensure SPEC` starts it (`qft docs` for the rest).
```

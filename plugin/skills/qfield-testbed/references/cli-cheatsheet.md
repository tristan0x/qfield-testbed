# qft cheat sheet

Global options go before the command: `qft [--json] [--agent-session ID] [--non-interactive] [-v] COMMAND …`.

| Command | Does |
|---|---|
| `qft doctor [--require CAP]` | host checks and capabilities |
| `qft docs [TOPIC]` | documentation of this version |
| `qft spec suggest [--path P] [--need N] [--output F]` | propose a spec for a checkout |
| `qft spec example`, `qft spec schema [--scenario]`, `qft spec validate [--scenario] FILE` | write and check specs and bisect scenarios |
| `qft deployment ensure SPEC [--name N\|auto] [--replace] [--timeout T] [--ttl T]` | create or reuse, and start |
| `qft deployment add SPEC`, `start NAME`, `stop NAME`, `restart NAME [--component C]` | step by step |
| `qft deployment list`, `show NAME` | what exists, URLs, credentials, revisions, owner, expiry |
| `qft deployment run NAME [--timeout T] -- CMD` | run with the `QFT_*` environment |
| `qft deployment env NAME [--format shell\|dotenv\|json]` | the `QFT_*` environment |
| `qft deployment logs NAME [--component C] [--service S] [--tail N] [--since T]` | container logs |
| `qft deployment launch NAME qgis\|android\|ios\|qfield-desktop` | interactive client (human) |
| `qft deployment keep NAME --ttl 3d\|none` | keep it longer |
| `qft deployment remove NAME --yes` | tear it down |
| `qft gc [--expired] [--orphaned] [--agent-session ID] [--dry-run] [--yes]` | clean up |
| `qft bisect example\|start\|run\|next\|good\|bad\|skip\|status\|list\|log\|reset\|remove` | bisect sessions |
| `qft mcp serve [--allow-run]` | the same operations as MCP tools (extra `[mcp]`) |
| `qft cache list\|prune [--dry-run]`, `qft config show\|path` | shared cache, configuration |

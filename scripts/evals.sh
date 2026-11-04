#!/bin/sh
# Run the agent evals locally, on the Claude subscription (never on a paid API key).
#   scripts/evals.sh [behaviour|scenario|all] [--only NAME,...] [--keep] [--model M]
# Needs a subscription token: `claude setup-token`, stored in ~/.config/qfield-testbed/claude-token (mode 600)
# or exported as CLAUDE_CODE_OAUTH_TOKEN. Results: evals/results/<timestamp>/summary.md
set -eu
cd "$(dirname "$0")/.."
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "error: ANTHROPIC_API_KEY is set: the evals would be billed to it. Unset it; they run on the subscription only." >&2
  exit 2
fi
kind="${1:-behaviour}"
case "$kind" in behaviour|scenario|all) shift || true ;; -*) kind=behaviour ;; *) echo "usage: $0 [behaviour|scenario|all] [options]" >&2; exit 2 ;; esac
command -v claude >/dev/null || { echo "error: the claude CLI is not installed" >&2; exit 2; }
uv run --quiet qft doctor >/dev/null 2>&1 || true   # warms the environment; each case checks what it needs itself
exec uv run --quiet python -m evals.runner --kind "$kind" "$@"

#!/usr/bin/env bash
# Stop hook: refuse to end a turn while the tree does not lint.
#
# Runs the same three checks as the "Lint" job in .github/workflows/ci.yml, so a
# task cannot be reported as finished in a state CI would reject. Tests are not
# run here — they are slower, and the "Tests" job in CI owns that gate.
#
# Exit 0 = let the turn end. Exit 2 = block, and feed stderr back to the agent.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# Claude Code sets stop_hook_active when the previous stop was already blocked by
# a hook. Blocking a second time in a row risks a loop, so surface the state and
# let the turn end — the agent has already been told once.
if grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' <<<"$(cat)"; then
  exit 0
fi

report=""
run() { # run <label> <cmd...>
  local label=$1 out
  shift
  if ! out=$("$@" 2>&1); then
    report+="--- ${label} (${*}) ---"$'\n'"${out}"$'\n\n'
  fi
}

run "ruff check"  uv run ruff check .
run "ruff format" uv run ruff format --check .
run "mypy"        uv run mypy src

if [[ -n $report ]]; then
  {
    echo "Lint gate failed — do not finish yet. Fix these, then re-run:"
    echo "  uv run ruff check --fix . && uv run ruff format . && uv run mypy src"
    echo
    printf '%s' "$report"
  } >&2
  exit 2
fi

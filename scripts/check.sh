#!/usr/bin/env bash
# The one gate: lint, format, secret scan, tests. Used by the pre-commit hook, CI and release.sh.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

RUFF="ruff"
command -v ruff >/dev/null 2>&1 || RUFF="python3 -m ruff"
$RUFF --version >/dev/null 2>&1 || { echo "check: ruff missing (pip install ruff)" >&2; exit 1; }

echo "check: ruff check";         $RUFF check .
echo "check: ruff format";        $RUFF format --check .
echo "check: secret scan";        python3 scripts/scan_secrets.py
echo "check: tests";              python3 -m unittest discover -s tests
if command -v hermes >/dev/null 2>&1; then
  echo "check: hermes plugins validate"
  for d in hyai-router model-providers/hostyourai; do out="$(hermes plugins validate "$d" --install-deps 2>&1)" || { echo "$out" >&2; echo "check: hermes validate failed for $d" >&2; exit 1; }; done
else
  echo "check: hermes not installed, skipping plugin validation (CI)"
fi
echo "check: OK"

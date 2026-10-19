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
echo "check: OK"

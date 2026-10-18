#!/usr/bin/env bash
# Installs the repo's git hooks (currently: pre-commit ruff lint/format check).
# Run once after cloning: ./scripts/install-git-hooks.sh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="$repo_root/.git/hooks"

ln -sf "../../scripts/pre-commit" "$hooks_dir/pre-commit"
chmod +x "$repo_root/scripts/pre-commit"

echo "Installed pre-commit hook -> scripts/pre-commit"

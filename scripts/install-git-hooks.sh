#!/usr/bin/env bash
# Installs the repo's git hooks (currently: pre-commit: the shared gate, scripts/check.sh).
# Run once after cloning: ./scripts/install-git-hooks.sh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="$repo_root/.git/hooks"

target="$hooks_dir/pre-commit"
if [ -e "$target" ] && [ ! -L "$target" ]; then
  echo "install-git-hooks: $target already exists and is not a symlink; leaving it alone." >&2
  echo "Move it aside and rerun to install the repo hook." >&2
  exit 1
fi
ln -sf "../../scripts/pre-commit" "$target"
chmod +x "$repo_root/scripts/pre-commit"

echo "Installed pre-commit hook -> scripts/pre-commit"

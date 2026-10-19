#!/usr/bin/env bash
# Usage: scripts/release.sh X.Y.Z [--no-push]
# Gate -> pin manifest to the current code commit -> commit -> tag vX.Y.Z -> push -> verify remote.
# Run from a clean main with the release's code changes already committed.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

VER="${1:?usage: release.sh X.Y.Z [--no-push]}"; PUSH=1; [ "${2:-}" = "--no-push" ] && PUSH=0
[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "release: version must be X.Y.Z" >&2; exit 1; }
[ "$(git branch --show-current)" = main ] || { echo "release: not on main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "release: working tree not clean" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/v$VER" >/dev/null && { echo "release: tag v$VER exists" >&2; exit 1; }
[ "$(git config user.email)" = "emil.varga@gmail.com" ] || { echo "release: git identity is not the expected author" >&2; exit 1; }  # scan: allow

git fetch -q origin main --tags
[ "$(git rev-list --count HEAD..origin/main)" = 0 ] || { echo "release: local main is behind origin/main" >&2; exit 1; }

scripts/check.sh
# 1) Release commit: plugin manifests + README badge carry the new version. This is the commit
#    the catalog/pack pins, so its plugin.yaml files must already say X.Y.Z.
sed -i -E "s/^version: .*/version: $VER/" hyai-router/plugin.yaml model-providers/hostyourai/plugin.yaml
sed -i -E "s#badge/version-v[0-9.]+-blue#badge/version-v$VER-blue#" README.md
for f in hyai-router/plugin.yaml model-providers/hostyourai/plugin.yaml; do
  grep -q "^version: $VER$" "$f" || { echo "release: $f version rewrite did not apply" >&2; git checkout -- .; exit 1; }
done
grep -q "badge/version-v$VER-blue" README.md || { echo "release: README version badge missing" >&2; git checkout -- .; exit 1; }
git add hyai-router/plugin.yaml model-providers/hostyourai/plugin.yaml README.md
git commit -q -m "release: v$VER"
SHA="$(git rev-parse HEAD)"
BASE="$(git rev-parse HEAD~1)"
scripts/check.sh || { git reset -q --hard "$BASE"; echo "release: gate failed on release commit, rolled back" >&2; exit 1; }

# 2) Pack manifest: pin both plugins to the release commit.
sed -i -E "s/^version: .*/version: $VER/; s/^( *ref: ).*/\\1$SHA/" hermes-pack.yaml
grep -q "^version: $VER$" hermes-pack.yaml && [ "$(grep -c "ref: $SHA" hermes-pack.yaml)" -ge 2 ] \
  || { echo "release: manifest rewrite did not apply (unexpected format?)" >&2; git reset -q --hard "$BASE"; exit 1; }
git add hermes-pack.yaml
git commit -q -m "hermes-pack: bump to v$VER, pin to ${SHA:0:7}"
python3 -m unittest tests.test_manifest_sync || { git reset -q --hard "$BASE"; echo "release: manifest test failed, release rolled back" >&2; exit 1; }
git tag -a "v$VER" -m "v$VER"
if [ "$PUSH" = 1 ]; then
  git push --atomic origin main "v$VER"
  [ "$(git ls-remote origin "refs/tags/v$VER^{}" | cut -f1)" = "$(git rev-parse "v$VER^{commit}")" ] \
    || { echo "release: remote tag does not match local" >&2; exit 1; }
fi
echo "release: v$VER pinned to ${SHA:0:7}$([ "$PUSH" = 1 ] && echo ', pushed and verified')"

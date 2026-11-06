"""Contract: hermes-pack.yaml, git tags and the single plugin.yaml stay in sync."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "hermes-pack.yaml"
PLUGIN_DIR = "hostyourai"


def git(*args: str) -> str | None:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def parse(text: str) -> tuple[str, list[str]]:
    version = re.search(r"^version:\s*(\S+)", text, re.M).group(1)
    return version, re.findall(r"^\s*ref:\s*(\S+)", text, re.M)


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


def field(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}:\s*[\"']?([^\"'\n#]+?)[\"']?\s*(?:#.*)?$", text, re.M)
    return m.group(1) if m else None


class ManifestSyncTests(unittest.TestCase):
    def setUp(self):
        self.version, self.refs = parse(MANIFEST.read_text())

    def test_version_is_semver_and_the_plugin_has_exactly_one_full_sha_pin(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(len(self.refs), 1, "exactly one plugin entry after the merge")
        self.assertRegex(self.refs[0], r"^[0-9a-f]{40}$", "pin must be a full SHA")

    def test_readme_version_badge_matches_manifest(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn(f"badge/version-v{self.version}-blue", readme)

    def test_pin_resolves_to_a_commit_in_this_repo(self):
        if git("rev-parse", "--git-dir") is None or git("cat-file", "-t", "HEAD") is None:
            self.skipTest("no git history")
        kind = git("cat-file", "-t", self.refs[0])
        if kind is None:
            self.skipTest("shallow clone: pinned commit not fetched")
        self.assertEqual(kind, "commit")

    def test_version_is_not_behind_latest_tag(self):
        tags = [t for t in (git("tag", "--list", "v*") or "").split() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
        if not tags:
            self.skipTest("no tags available")
        self.assertGreaterEqual(vtuple(self.version), max(vtuple(t) for t in tags))

    def test_existing_tag_for_this_version_carries_the_same_manifest(self):
        shown = git("show", f"v{self.version}:hermes-pack.yaml")
        if shown is None:
            self.skipTest("tag for this version not created yet")
        self.assertEqual(parse(shown), (self.version, self.refs))


class PluginManifestTests(unittest.TestCase):
    """plugin.yaml is what the catalog reads: it must be truthful (admission rule 14)."""

    def setUp(self):
        self.version, self.refs = parse(MANIFEST.read_text())
        self.plugin_text = (ROOT / PLUGIN_DIR / "plugin.yaml").read_text()

    def test_plugin_version_matches_pack_version(self):
        self.assertEqual(field(self.plugin_text, "version"), self.version)

    def test_pinned_commit_carries_matching_plugin_version(self):
        if vtuple(self.version) < (0, 1, 7):
            self.skipTest("v0.1.6 and earlier were pinned before plugin.yaml carried the version")
        if git("cat-file", "-t", self.refs[0]) != "commit":
            self.skipTest("pinned commit not available (shallow clone)")
        shown = git("show", f"{self.refs[0]}:{PLUGIN_DIR}/plugin.yaml")
        if shown is None:
            self.skipTest(
                f"pin not yet re-pointed at a commit containing {PLUGIN_DIR}/ "
                "(release.sh updates the pin at release time)"
            )
        self.assertEqual(field(shown, "version"), self.version)

    def test_requires_hermes_is_a_semver_floor_not_calver(self):
        spec = field(self.plugin_text, "requires_hermes")
        self.assertIsNotNone(spec, "requires_hermes must be declared")
        m = re.fullmatch(r">=(\d+)\.(\d+)(?:\.(\d+))?", spec.strip())
        self.assertIsNotNone(m, f"{spec!r} is not a SemVer floor")
        self.assertLess(int(m.group(1)), 2000, "CalVer date is not allowed")

    def test_manifest_v2_identity_fields(self):
        self.assertEqual(field(self.plugin_text, "manifest_version"), "2")
        self.assertEqual(field(self.plugin_text, "license"), "MIT")
        self.assertEqual(field(self.plugin_text, "author"), "cogitor")
        self.assertTrue(field(self.plugin_text, "homepage").startswith("https://github.com/cogitor/"))

    def test_provider_declares_its_api_key(self):
        self.assertIn("HOSTYOURAI_API_KEY", self.plugin_text)


if __name__ == "__main__":
    unittest.main()

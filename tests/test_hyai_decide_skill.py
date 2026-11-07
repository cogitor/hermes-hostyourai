"""Checks for hostyourai/skills/hyai-decide/SKILL.md.

Covers what a generic SKILL.md linter won't: internal consistency between the skill's
documented API surface and the real ``decision_client`` module it teaches agents to call.
Frontmatter parsing here is deliberately regex-based (no YAML parser is installed in this
repo's runtime; see test_manifest_sync.py for the same convention).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = ROOT / "hostyourai" / "skills" / "hyai-decide" / "SKILL.md"
CLIENT_PATH = ROOT / "hostyourai" / "decision_client.py"


def split_frontmatter(text: str) -> tuple[dict, str]:
    assert text.startswith("---\n"), "frontmatter must start at byte 0"
    end = text.index("\n---\n", 4)
    fm_text, body = text[4:end], text[end + 5 :]
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm, body


class FrontmatterTests(unittest.TestCase):
    """Hermes's own frontmatter schema (see the skill manager tool's validator) plus
    the repo's hardline authoring standard (hermes-agent-skill-authoring skill)."""

    def setUp(self):
        self.text = SKILL_PATH.read_text()
        self.fm, self.body = split_frontmatter(self.text)

    def test_closes_properly_and_has_non_empty_body(self):
        self.assertIn("\n---\n", self.text)
        self.assertTrue(self.body.strip())

    def test_required_fields_present(self):
        for key in ("name", "description"):
            self.assertIn(key, self.fm, f"missing required field {key!r}")

    def test_name_matches_directory_and_is_kebab_case(self):
        self.assertEqual(self.fm["name"], "hyai-decide")
        self.assertRegex(self.fm["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(self.fm["name"]), 64)

    def test_description_is_quoted_and_within_validator_ceiling(self):
        raw = self.fm["description"]
        # Hermes validator ceiling is 1024 chars; this repo's hardline target is far
        # tighter (60 chars) but is only enforced in review for *bundled* skills, not
        # mechanically -- this plugin's skill ships with the plugin, not in-tree in
        # hermes-agent core, so only the hard validator ceiling is asserted here.
        self.assertLessEqual(len(raw.strip('"')), 1024)
        self.assertTrue(
            raw.startswith('"') and raw.endswith('"'), "description contains ':' and must be quoted"
        )

    def test_version_is_semver(self):
        self.assertRegex(self.fm.get("version", ""), r"^\d+\.\d+\.\d+$")

    def test_platforms_declared_and_plausible(self):
        platforms = self.fm.get("platforms", "")
        self.assertTrue(platforms)
        # The skill's own body only uses stdlib-only Python via execute_code/terminal,
        # so it should not be narrowed to a single OS.
        self.assertIn("linux", platforms)


README_PATH = ROOT / "README.md"


class CodeExampleConsistencyTests(unittest.TestCase):
    """The skill teaches a public API surface; every symbol/kwarg it shows must exist
    in the real decision_client module, and vice versa for the parts that matter."""

    def setUp(self):
        self.skill_text = SKILL_PATH.read_text()
        self.client_text = CLIENT_PATH.read_text()

    def test_imported_symbols_exist_in_the_real_module(self):
        m = re.search(r"from hostyourai\.decision_client import ([\w, ]+)", self.skill_text)
        self.assertIsNotNone(m, "skill must show the real import line")
        for name in (s.strip() for s in m.group(1).split(",")):
            self.assertIn(
                f"def {name}(", self.client_text, f"{name!r} shown in skill but not defined in module"
            )

    def test_ask_kwargs_used_in_examples_are_real_parameters(self):
        sig = re.search(r"def ask\(([^)]*)\)", self.client_text, re.S).group(1)
        real_params = set(re.findall(r"(\w+)\s*(?::|=)", sig)) | {"questions", "state"}
        used = set()
        for call in re.finditer(r"\bask\(\s*(.*?)\n\)", self.skill_text, re.S):
            # only top-level kwargs of the ask(...) call itself, not nested dict keys
            depth = 0
            buf = ""
            top_level_kwargs = []
            for ch in call.group(1):
                if ch in "{[(":
                    depth += 1
                elif ch in "}])":
                    depth -= 1
                if depth == 0:
                    buf += ch
            top_level_kwargs = re.findall(r"(?:^|,)\s*(\w+)\s*=", buf)
            used |= set(top_level_kwargs)
        unknown = used - real_params
        self.assertEqual(unknown, set(), f"skill uses kwargs ask() doesn't have: {unknown}")

    def test_exception_types_named_in_skill_are_real(self):
        builtins_and_module_exceptions = {"ValueError", "TypeError", "DecisionError"}
        for exc in re.findall(r"`(\w*Error)`", self.skill_text):
            self.assertIn(exc, builtins_and_module_exceptions, f"unrecognized exception type {exc!r}")
        self.assertIn("class DecisionError", self.client_text)

    def test_default_model_and_url_match_the_module_constants(self):
        model_match = re.search(r'DEFAULT_MODEL = "([^"]+)"', self.client_text)
        url_match = re.search(r'DEFAULT_URL = "([^"]+)"', self.client_text)
        self.assertIn(model_match.group(1), self.skill_text)
        self.assertIn(url_match.group(1), self.skill_text)

    def test_confidence_of_is_documented_consistently_with_its_docstring(self):
        self.assertIn("confidence_of", self.skill_text)
        # noul has no confidence field per both the module docstring and the skill table
        self.assertIn("no `confidence` field", self.skill_text)
        self.assertIn("None for Noul", CLIENT_PATH.read_text())

    def test_env_var_name_matches(self):
        env_match = re.search(r'env_var: str = "([^"]+)"', self.client_text)
        self.assertIn(env_match.group(1), self.skill_text)

    def test_question_types_in_skill_match_module_validation(self):
        valid = re.search(r'"choice", "score", "noul"', self.client_text)
        self.assertIsNotNone(valid, "module's allowed types changed; update skill's type table")
        for t in ("choice", "score", "noul"):
            self.assertIn(f"| {t} ", self.skill_text)


class ReadmeExampleConsistencyTests(unittest.TestCase):
    """README.md has its own hyai-decide usage snippet, separate from SKILL.md's --
    it drifted out of sync with the real API once already (a fabricated DecisionClient
    class), so it gets the same import/kwarg checks CodeExampleConsistencyTests runs
    against SKILL.md."""

    def setUp(self):
        self.readme_text = README_PATH.read_text()
        self.client_text = CLIENT_PATH.read_text()

    def test_readme_does_not_reference_a_nonexistent_decisionclient_class(self):
        self.assertNotIn("DecisionClient", self.readme_text)

    def test_readme_imported_symbols_exist_in_the_real_module(self):
        m = re.search(r"from hostyourai\.decision_client import ([\w, ]+)", self.readme_text)
        self.assertIsNotNone(m, "README must show the real import line")
        for name in (s.strip() for s in m.group(1).split(",")):
            self.assertIn(
                f"def {name}(",
                self.client_text,
                f"{name!r} shown in README but not defined in module",
            )

    def test_readme_ask_kwargs_used_are_real_parameters(self):
        sig = re.search(r"def ask\(([^)]*)\)", self.client_text, re.S).group(1)
        real_params = set(re.findall(r"(\w+)\s*(?::|=)", sig)) | {"questions", "state"}
        call = re.search(r"\bask\(\s*(.*?)\n\)", self.readme_text, re.S)
        self.assertIsNotNone(call, "README must show a real ask(...) call")
        depth = 0
        buf = ""
        for ch in call.group(1):
            if ch in "{[(":
                depth += 1
            elif ch in "}])":
                depth -= 1
            if depth == 0:
                buf += ch
        used = set(re.findall(r"(?:^|,)\s*(\w+)\s*=", buf))
        unknown = used - real_params
        self.assertEqual(unknown, set(), f"README uses kwargs ask() doesn't have: {unknown}")


if __name__ == "__main__":
    unittest.main()

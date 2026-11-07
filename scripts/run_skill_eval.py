#!/usr/bin/env python3
"""Run a skill's data-driven eval cases against a live Hermes CLI session.

Reads ``evals/prompts.yaml`` + ``evals/rubric.yaml`` next to the given skill
and drives ``hermes chat -q`` once per case, from inside a directory where the
skill is trusted as a project-local skill (``hermes skills trust .``). No
prompt text is hardcoded here — all test data lives in the YAML files, so new
cases are added by editing YAML, not this script.

This intentionally does NOT scrape a REASONING:/COMMANDS: text contract from
the model (that was an earlier, less reliable approach prototyped against a
generic Ollama-style harness). Instead it inspects the real CLI reply: whether
the skill name appears in the text is used as the trigger signal, since a
project-local skill loads silently and the model has no other way to name it
unless it decided to use it.

Usage:
    python3 scripts/run_skill_eval.py --skill-path hostyourai/skills/hyai-decide \
        --model hyai/auto --provider hostyourai

Requires HOSTYOURAI_API_KEY (or whatever the target provider needs) to already
be present in the environment -- this script never reads, prints, or logs any
secret value itself.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

import yaml


def load_yaml(path: pathlib.Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing required eval file: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_case(repo_root: pathlib.Path, prompt_text: str, model: str, provider: str) -> str:
    """Run one prompt through `hermes chat -q` and return the raw reply text."""
    full_prompt = (
        f'The user asks: "{prompt_text}" '
        "Start your reply with exactly one line: 'USE: <skill-name>' if you would use a "
        "named skill to answer this, or 'USE: none' if no skill applies or a skill's own "
        "rules tell you not to use it for this case. Then explain briefly."
    )
    proc = subprocess.run(
        [
            "hermes",
            "chat",
            "-q",
            full_prompt,
            "-m",
            model,
            "--provider",
            provider,
            "-Q",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "TERMINAL_CWD": str(repo_root)},
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"hermes chat exited {proc.returncode}: {detail[:500]}")
    return proc.stdout.strip()


def score_case(case: dict, reply: str, skill_name: str) -> tuple[bool, str]:
    should_trigger = case.get("should_trigger", True)
    first_line = reply.strip().splitlines()[0] if reply.strip() else ""
    use_match = re.match(r"USE:\s*(.+)", first_line, re.IGNORECASE)
    if use_match:
        triggered = skill_name.lower() in use_match.group(1).strip().lower()
    else:
        # Model didn't follow the USE: convention -- fall back to a whole-reply check.
        triggered = skill_name.lower() in reply.lower()

    if should_trigger and not triggered:
        return False, f"expected skill '{skill_name}' to be used/named, but it was not mentioned"
    if not should_trigger and triggered:
        return False, f"skill '{skill_name}' was named when it should not have triggered"

    for needle in case.get("expected_reasoning_contains", []) or []:
        if needle.lower() not in reply.lower():
            return False, f"reply missing expected phrase: {needle!r}"

    for needle in case.get("forbidden_commands", []) or []:
        if needle.lower() in reply.lower():
            return False, f"reply contains forbidden text: {needle!r}"

    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill-path", required=True, help="Path to the skill dir, e.g. hostyourai/skills/hyai-decide"
    )
    parser.add_argument("--model", required=True, help="Model name, e.g. hyai/auto")
    parser.add_argument("--provider", required=True, help="Provider name, e.g. hostyourai")
    parser.add_argument(
        "--repo-root", default=".", help="Directory to run `hermes chat` from (must have the skill trusted)"
    )
    args = parser.parse_args()

    skill_dir = pathlib.Path(args.skill_path).resolve()
    repo_root = pathlib.Path(args.repo_root).resolve()
    evals_dir = skill_dir / "evals"
    skill_name = skill_dir.name

    prompts_data = load_yaml(evals_dir / "prompts.yaml")
    rubric_data = load_yaml(evals_dir / "rubric.yaml")
    cases = prompts_data.get("prompts", [])
    category_weights = {c["id"]: c["weight"] for c in rubric_data.get("checks", [])}
    pass_threshold = rubric_data.get("scoring", {}).get("pass_threshold", 70)
    unknown_categories = {c.get("category", "uncategorized") for c in cases} - set(category_weights)
    if unknown_categories:
        print(
            f"ERROR: prompts.yaml uses categor{'y' if len(unknown_categories) == 1 else 'ies'} "
            f"{sorted(unknown_categories)} not present in rubric.yaml's weights {sorted(category_weights)} "
            "— fix the typo/add the category to rubric.yaml before scoring means anything.",
            file=sys.stderr,
        )
        return 2

    print(f"Running {len(cases)} case(s) for skill '{skill_name}' against {args.model} ({args.provider})...")
    print()

    category_totals: dict[str, int] = {}
    category_passed: dict[str, int] = {}
    any_error = False

    for i, case in enumerate(cases, 1):
        case_id = case.get("id", f"case-{i}")
        category = case.get("category", "uncategorized")
        prompt_text = case["prompt"]

        print(f"  [{i}/{len(cases)}] {case_id} ... ", end="", flush=True)
        try:
            reply = run_case(repo_root, prompt_text, args.model, args.provider)
        except Exception as exc:  # noqa: BLE001 - surface any failure as a failed case
            print(f"ERROR: {exc}")
            any_error = True
            category_totals[category] = category_totals.get(category, 0) + 1
            continue

        passed, reason = score_case(case, reply, skill_name)
        category_totals[category] = category_totals.get(category, 0) + 1
        if passed:
            category_passed[category] = category_passed.get(category, 0) + 1
            print("PASS")
        else:
            print(f"FAIL — {reason}")

    print()
    print("=" * 70)
    print(f" SKILL EVAL: {skill_name}")
    print("=" * 70)

    total_weight = sum(category_weights.values()) or 100
    weighted_score = 0.0
    for category, total in category_totals.items():
        passed = category_passed.get(category, 0)
        rate = (passed / total * 100) if total else 0.0
        weight = category_weights.get(category, 0)
        weighted_score += rate * weight / total_weight if total_weight else 0
        status = "OK" if passed == total else "FAIL"
        print(f"  {status:5} {category:15} {passed}/{total}  ({rate:.1f}%)  weight={weight}")

    print()
    verdict = "PASS" if weighted_score >= pass_threshold else "FAIL"
    print(f"  Overall score: {weighted_score:.1f}/100  [{verdict}]  (threshold {pass_threshold})")

    if any_error:
        print()
        print("One or more cases errored out (see ERROR lines above) — treat score as incomplete.")

    return 0 if verdict == "PASS" and not any_error else 1


if __name__ == "__main__":
    sys.exit(main())

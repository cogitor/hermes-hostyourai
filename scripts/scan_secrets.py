#!/usr/bin/env python3
"""Generic secret/PII scan over git-tracked files (staged files included).

Patterns are shapes only (key-like strings, home paths, phone/email shapes). Never add a real
identifier here: that would leak it. Use `# scan: allow` on a line to whitelist a deliberate fake.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "api-key-like token": re.compile(r"\b(?:sk|pk|ghp|gho|ghs|xox[bap]|AKIA)[-_A-Za-z0-9]{16,}"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{12,}"),
    "key assignment": re.compile(
        r"(?i)(?:api[_-]?key|secret|token|passw(?:or)?d)[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9+/_.~-]{12,}"
    ),
    # A prefixed name (X_KEY) only counts when the value looks random: digits plus letters.
    "prefixed key assignment": re.compile(
        r"(?i)\b\w{1,40}_key[\"']?\s*[=:]\s*[\"']?(?=[A-Za-z0-9+/_.~-]*\d)(?=[A-Za-z0-9+/_.~-]*[A-Za-z])[A-Za-z0-9+/_.~-]{16,}"
    ),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "home directory path": re.compile(
        r"(?i:/(?:home|Users)/(?!<|\$|user\b|runner\b|name\b)[a-z][a-z0-9_-]+/|/root/)"
    ),
    "email address": re.compile(r"\b[\w.+-]+@(?!example\.|users\.noreply\.github\.com)[\w-]+\.[a-z]{2,}\b"),
    "phone number": re.compile(r"(?<!\d)\+\d{1,3}(?:[ .-]?\d){8,13}(?!\d)"),
    "tailnet hostname": re.compile(r"[\w-]{1,63}\.ts\.net\b"),
    "private ip address": re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}\b"
    ),
    "telegram-style chat id": re.compile(r"\b(?:chat_?id|user_?id)\b\D{0,6}\d{7,}"),
}
MAX_LINE = 2000
SKIP_SUFFIXES = (".lock",)
SELF = "scripts/scan_secrets.py"


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        f
        for f in out.split("\0")
        if f and f != SELF and not f.endswith(SKIP_SUFFIXES) and not os.path.islink(f)
    ]


def scan(path: str) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []  # deleted in the working tree but still in the index
    except UnicodeDecodeError:
        return [f"{path}: binary (non-UTF-8) file is tracked; this repo should hold text only"]
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if "scan: allow" in line:
            continue
        if len(line) > MAX_LINE:  # minified/lock-style line: bound regex work, still check its head
            line = line[:MAX_LINE]
        for name, rx in PATTERNS.items():
            if rx.search(line):
                hits.append(f"{path}:{n}: {name}")
    return hits


def main() -> int:
    hits = [h for f in tracked_files() for h in scan(f)]
    for h in hits:
        print(h, file=sys.stderr)
    if hits:
        print(
            f"scan_secrets: {len(hits)} finding(s). Remove them or mark a deliberate fake `# scan: allow`.",
            file=sys.stderr,
        )
        return 1
    print("scan_secrets: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

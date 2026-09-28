#!/usr/bin/env python3
"""Fail when a file looks like it holds a real credential.

Two callers, one rule set: the `no-credentials` pre-commit hook (which passes the
staged paths) and `make secrets` (no arguments: every tracked file). Keeping the
rules in one place means the gate and the manual target cannot drift apart.

Two failure modes matter, and both are covered here:

* a secret written into a tracked file, including a JWT-shaped access or anon
  token and a password embedded in a Postgres URL;
* a `.env*` file that is *about to be committed at all* — gitignored or not,
  `git add -f` is one keystroke away from publishing it.

Placeholders are allowed on purpose. `.env.example` is the template and the docs
spell the connection string as `postgresql://postgres.<ref>:<password>@…`, so a
value that looks illustrative (`<password>`, `$DB_PASSWORD`, `change-me-…`,
`your-anon-public-key`) is not a finding. Only values that look real are.

Nothing that matched is ever printed: the report names the file, the line and
what the line *is*, never the secret — a hook that echoes the key it found has
just written the key to a log.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Illustrative values. A real credential contains none of these markers, so
# skipping them keeps the check free of the false positives that would make
# everyone run it with `--no-verify`.
PLACEHOLDER = re.compile(
    r"[<>${}]|^(your|change|example|test|dummy|placeholder|xxx|todo|redacted|none|null)", re.I
)

#: (what the finding is, pattern, group holding the value to sanity-check).
RULES: tuple[tuple[str, re.Pattern[str], int], ...] = (
    (
        "assigns a secret-looking value",
        re.compile(r"\b(APP_ENCRYPTION_KEY|DOCS_PASSWORD|SUPABASE_SERVICE_ROLE_KEY)\s*=\s*(\S+)"),
        2,
    ),
    (
        # Header, payload and signature: every Supabase access/anon token. The
        # header is always long (`{"alg":"HS256","typ":"JWT"}`), but a small
        # payload (`{"sub":"…"}`) base64-encodes to barely a dozen characters,
        # so the lower bounds stay low enough to catch a real token.
        "contains a JWT (access or anon token)",
        re.compile(r"eyJ[\w-]{15,}\.eyJ[\w-]{10,}\.[\w-]{10,}"),
        0,
    ),
    ("contains a Supabase secret key", re.compile(r"\bsb_secret_[\w-]{10,}"), 0),
    (
        "embeds a password in a Postgres URL",
        re.compile(r"postgres(?:ql)?://[^:/\s]+:([^@\s]+)@"),
        1,
    ),
)

#: The one env file that is meant to be committed; `.env` itself never is.
ALLOWED_ENV_FILE = ".env.example"

#: `.env.example`-style templates and lockfiles hold no secrets of ours.
ALLOWED_SUFFIXES = (".example",)

#: Above this, a file is data (or generated), not something a secret is pasted into.
MAX_BYTES = 2 * 1024 * 1024

REMEDY = (
    "remove the value from the file, rotate the credential, and keep it in the "
    "gitignored .env or with `fastapi cloud env set --secret <NAME> <value>`"
)


def _is_placeholder(value: str) -> bool:
    """True for illustrative values such as ``<password>`` or ``change-me``."""
    return bool(PLACEHOLDER.search(value))


def _tracked_files() -> list[Path]:
    """Every tracked file, for a no-argument run (i.e. `make secrets`)."""
    result = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, check=True)
    return [Path(name) for name in result.stdout.split("\0") if name]


def _paths(argv: list[str]) -> list[Path]:
    """The files to scan: the arguments, or every tracked file when there are none."""
    if argv:
        return [Path(arg) for arg in argv]
    try:
        return _tracked_files()
    except (OSError, subprocess.CalledProcessError):  # not a git checkout
        return sorted(path for path in Path(".").rglob("*") if path.is_file())


def scan(path: Path) -> list[str]:
    """Return one message per finding in `path` (empty when it is clean)."""
    if path.name.startswith(".env") and path.name != ALLOWED_ENV_FILE:
        return [f"{path}: this file must never be committed - {REMEDY}"]

    if path.suffix in ALLOWED_SUFFIXES or not path.is_file():
        return []
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []  # binary or unreadable: nothing for a text scanner to say

    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for description, pattern, group in RULES:
            for match in pattern.finditer(line):
                if _is_placeholder(match.group(group)):
                    continue
                findings.append(f"{path}:{number}: {description} - {REMEDY}")
    return findings


def main(argv: list[str] | None = None) -> int:
    """Scan the given files (or every tracked file) and report what looks secret."""
    findings = [
        finding
        for path in _paths(list(argv if argv is not None else sys.argv[1:]))
        for finding in scan(path)
    ]

    for finding in findings:
        print(finding)
    if findings:
        print(f"\n{len(findings)} possible credential(s) found.")
        return 1
    print("No credentials found in the scanned files.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())

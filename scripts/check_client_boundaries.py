#!/usr/bin/env python3
"""Keep the Supabase client in exactly one place, bound to exactly one caller.

This is AGENTS.md rule 2 (and the RLS guarantee behind it) turned into a check:

* ``create_client`` may only be used by ``app/core/clients/supabase.py`` — one
  factory, and the only module that knows how to read credentials. A route that
  builds its own client bypasses the lazy-init guard and breaks app startup
  without credentials.
* ``get_supabase_client`` (the cached, service-level client) may only be used by
  ``app/core/`` and ``app/modules/auth/``. Data routes must depend on
  ``get_current_client`` instead: that client carries the caller's JWT, so
  Postgres RLS evaluates as that user. A data route that reaches for the shared
  client reads and writes as the wrong identity — with the anon key, as nobody.

Findings come from the AST, not from a text search, so prose in a comment or a
docstring (this repository has plenty) never trips it. A file that does not parse
is skipped: `check-ast` reports that separately, and duplicating the error here
would only add noise to the same run.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Where each name is allowed to appear. A trailing slash means "this directory".
ALLOWED: dict[str, tuple[str, ...]] = {
    "create_client": ("app/core/clients/supabase.py",),
    "get_supabase_client": ("app/core/", "app/modules/auth/", "app/modules/customer_portal/"),
}

SEARCH_ROOT = Path("app")

HELP = {
    "create_client": "only app/core/clients/supabase.py builds clients (get_supabase_client / "
    "get_client_for_token), so the credentials live in exactly one place",
    "get_supabase_client": "only app/core/ and app/modules/auth/ may use the shared client; "
    "data routes must depend on get_current_client so RLS runs as the caller "
    "(AGENTS.md rule 2)",
}


def _identifiers(tree: ast.AST) -> dict[str, int]:
    """Map every referenced name in `tree` to the line it was seen on."""
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.extend([alias.name, alias.asname or ""])
        for name in names:
            if name in ALLOWED:
                found.setdefault(name, node.lineno)
    return found


def _allowed(name: str, path: Path) -> bool:
    """True when this file is one of the places `name` may appear.

    Compared as repo-relative text rather than through the filesystem, so the
    verdict cannot depend on the working directory the hook happened to run in.
    """
    text = path.as_posix()
    return any(
        text == entry or (entry.endswith("/") and text.startswith(entry)) for entry in ALLOWED[name]
    )


def scan(path: Path) -> list[str]:
    """Return one message per misplaced client reference in `path`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []  # unreadable or already reported by `check-ast`

    return [
        f"{path}:{line}: {name} does not belong here - {HELP[name]}"
        for name, line in sorted(_identifiers(tree).items(), key=lambda item: item[1])
        if not _allowed(name, path)
    ]


def _paths(argv: list[str]) -> list[Path]:
    """The files to check: the arguments, or every module under `app/`."""
    if argv:
        return [Path(arg) for arg in argv]
    return sorted(SEARCH_ROOT.rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    """Report every client that is created or shared outside its home module."""
    findings = [
        finding
        for path in _paths(list(argv if argv is not None else sys.argv[1:]))
        for finding in scan(path)
    ]

    for finding in findings:
        print(finding)
    if findings:
        print(f"\n{len(findings)} client boundary violation(s).")
        return 1
    print("Supabase clients are created and shared in the right places.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())

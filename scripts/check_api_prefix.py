#!/usr/bin/env python3
"""Keep the ``/api/<version>`` prefix in the registry, out of the routers.

``app/modules/registry.py`` prepends ``settings.api_prefix`` when it mounts a
router, and it derives that from ``API_VERSION``. A module that hard-codes the
prefix instead — ``APIRouter(prefix="/api/v1/things")`` — gets a second, private
copy of the version that silently ignores ``API_VERSION``: bumping the setting
would then publish the new surface *and* leave the old one alive on the same
deployment, which is exactly the bug AGENTS.md rule 5 exists to prevent.

The check reads the AST, so only a real ``prefix="/api/…"`` argument counts; the
version is spelled out in prose all over this repository (docstrings, the docs,
this file) and none of that is a prefix. Only string literals are inspected: a
prefix assembled at runtime, such as ``prefix="/api/" + version``, would need the
same rule applied by hand in review.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Calls whose `prefix=` argument must stay version-free.
CALLS = frozenset({"APIRouter", "include_router", "add_api_route", "add_api_websocket_route"})

SEARCH_ROOT = Path("app")

HINT = (
    "let the registry add it: give the router a version-free prefix and set `version=` in MODULES"
)


def _callee(node: ast.Call) -> str:
    """Name of the function being called, whether plain or attribute-style."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def scan(path: Path) -> list[str]:
    """Return one message per hard-coded API prefix in `path`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []  # unreadable or already reported by `check-ast`

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node) not in CALLS:
            continue
        for keyword in node.keywords:
            if keyword.arg != "prefix" or not isinstance(keyword.value, ast.Constant):
                continue
            value = keyword.value.value
            if isinstance(value, str) and "/api/" in value:
                findings.append(
                    f"{path}:{node.lineno}: prefix={value!r} hard-codes the API surface - {HINT}"
                )
    return findings


def _paths(argv: list[str]) -> list[Path]:
    """The files to check: the arguments, or every module under `app/`."""
    if argv:
        return [Path(arg) for arg in argv]
    return sorted(SEARCH_ROOT.rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    """Report every router that hard-codes the versioned API prefix."""
    findings = [
        finding
        for path in _paths(list(argv if argv is not None else sys.argv[1:]))
        for finding in scan(path)
    ]

    for finding in findings:
        print(finding)
    if findings:
        print(f"\n{len(findings)} hard-coded API prefix(es).")
        return 1
    print("The API prefix comes from the registry everywhere.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())

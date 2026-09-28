#!/usr/bin/env python3
"""Syntax-check the inline JavaScript in `app/static/index.html`.

That file is the whole dashboard: one self-contained page, no bundler, no
build step, no second copy of the script anywhere. A single stray brace, an
unclosed string or a curly quote therefore breaks the *entire* UI at once — the
page still renders, the theme still applies, and every button quietly stops
working. Nothing else in the toolchain would notice, so this runs before the
commit that introduced it.

Node is the parser (`node --check`), which means node must be installed: it is
required to work on this file at all, so a missing binary is reported as a
failure with the fix rather than skipped silently. Node reports positions in the
extracted script, which is useless on a 2,500-line page — the line is translated
back to the HTML file so the report points at something you can open.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PAGE = Path("app/static/index.html")

#: Only bare `<script>` blocks are checked: `type="application/json"` payloads
#: are data, and this page has no external scripts at all.
SCRIPT_BLOCK = re.compile(r"<script>(.*?)</script>", re.S)

#: `path/to/file.js:1234` — how node points at the offending line.
NODE_LINE = re.compile(r":(\d+)")

NODE_MISSING = (
    "node is required to syntax-check the inline JavaScript - "
    "install Node.js (or run `make js-check` on a machine that has it)"
)


def extract(html: str) -> tuple[str, list[int]]:
    """Split the page into script source, plus a map from its lines to HTML lines."""
    source: list[str] = []
    #: joined line number -> line in index.html (1-based, index 0 unused).
    line_map: list[int] = [0]

    for match in SCRIPT_BLOCK.finditer(html):
        first_line = html.count("\n", 0, match.start(1)) + 1
        for offset, _ in enumerate(match.group(1).splitlines()):
            line_map.append(first_line + offset)
        source.extend(match.group(1).splitlines())

    return "\n".join(source), line_map


#: The parser's verdict: `SyntaxError: Unexpected token ';'` and friends.
NODE_VERDICT = re.compile(r"^[A-Z]\w*Error:")


def _report(node_error: str, line_map: list[int], page: Path) -> list[str]:
    """Rewrite node's line numbers as lines in the HTML file.

    Node prints the location, the offending source line and then a stack trace;
    only the first two are useful, so they are folded into one line:
    `index.html:1967: const broken = {; -> SyntaxError: Unexpected token ';'`.
    """
    lines = [line.strip() for line in node_error.splitlines() if line.strip()]
    if not lines:
        return [f"{page}: the JavaScript parser failed without a message"]

    match = NODE_LINE.search(lines[0])
    index = int(match.group(1)) if match else 0
    where = f"{page}:{line_map[index]}" if index < len(line_map) else str(page)

    context = lines[1] if len(lines) > 1 and not NODE_VERDICT.match(lines[1]) else ""
    verdict = next((line for line in lines if NODE_VERDICT.match(line)), lines[0])
    return [f"{where}: {f'{context} -> ' if context else ''}{verdict}"]


def scan_html(html: str, page: Path = PAGE) -> list[str]:
    """Return one message per problem the JavaScript parser reports."""
    if shutil.which("node") is None:
        return [NODE_MISSING]

    source, line_map = extract(html)
    if not source.strip():
        return [
            f"{page}: no inline <script> block found - the page is supposed to be self-contained"
        ]

    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as script:
        script.write(source)
        script.flush()
        result = subprocess.run(["node", "--check", script.name], capture_output=True, text=True)

    if result.returncode == 0:
        return []
    return _report(result.stderr or result.stdout, line_map, page)


def _paths(argv: list[str]) -> list[Path]:
    """The pages to check: the arguments, or the one page this repo ships."""
    return [Path(arg) for arg in argv] if argv else [PAGE]


def main(argv: list[str] | None = None) -> int:
    """Parse every inline script block and report syntax errors."""
    findings: list[str] = []
    for path in _paths(list(argv if argv is not None else sys.argv[1:])):
        try:
            findings.extend(scan_html(path.read_text(encoding="utf-8"), path))
        except OSError as error:
            findings.append(f"{path}: {error}")

    for finding in findings:
        print(finding)
    if findings:
        print(f"\n{len(findings)} inline JavaScript problem(s).")
        return 1
    print("Inline JavaScript parses.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())

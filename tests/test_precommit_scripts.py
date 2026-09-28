"""Tests for the repository checks behind the local pre-commit hooks.

Each hook is a rule from AGENTS.md made executable, so what matters is pinned
here: every check passes the real tree, and every check fails — loudly and
usefully — on the thing it exists to catch. A hook that only ever runs green is
worse than no hook, because the gate then *looks* enforced.

Fixtures that must trip a rule are assembled from parts on purpose. A literal
fake secret in this file would be found by the very check it is testing, since
`make secrets` scans the tracked tree and this file is tracked.
"""

from pathlib import Path

import pytest

import scripts.check_api_prefix as check_api_prefix
import scripts.check_client_boundaries as check_client_boundaries
import scripts.check_credentials as check_credentials
import scripts.check_inline_js as check_inline_js

# Assembled so this test file does not itself read as a leak.
SECRET_ASSIGNMENT = "APP_ENCRYPTION_" + "KEY=" + "s3cret-value-1234"
JWT_LIKE = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 12
POSTGRES_URL = "postgresql://" + "postgres.abcdef:" + "hunter2hunter2" + "@db.example.com/postgres"
SUPABASE_SECRET = "sb_secret_" + "d" * 24


def _write(tmp_path: Path, name: str, content: str) -> str:
    """Write a file next to the test and hand back its path as a string."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


# --- the checks must pass the repository they protect ----------------------


def test_every_check_passes_the_real_tree(capsys):
    """No arguments means "the whole repository" — the gate must be green on it."""
    assert check_credentials.main([]) == 0
    assert check_client_boundaries.main([]) == 0
    assert check_api_prefix.main([]) == 0
    assert check_inline_js.main([]) == 0
    assert "No credentials found" in capsys.readouterr().out


# --- credentials -----------------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    [
        SECRET_ASSIGNMENT,
        JWT_LIKE,
        SUPABASE_SECRET,
        POSTGRES_URL,
        f"SUPABASE_SERVICE_ROLE_KEY={SUPABASE_SECRET}",
    ],
)
def test_a_secret_written_into_a_file_is_reported(tmp_path, capsys, leak):
    path = _write(tmp_path, "settings.py", f'KEY = "{leak}"\n')

    assert check_credentials.main([path]) == 1

    report = capsys.readouterr().out
    assert f"{path}:1:" in report
    # The report says *what* was found, never the value itself: a hook that
    # echoes the credential has just written it to another log.
    assert leak not in report


@pytest.mark.parametrize(
    "placeholder",
    [
        "APP_ENCRYPTION_KEY" + "=" + "change-me-to-a-long-random-str",
        "DOCS_PASSWORD" + "=" + "your-strong-passwor",
        "APP_ENCRYPTION_KEY" + "=" + "",
        "DB_URL="
        + "postgresql://"
        + "postgres.abcdef:"
        + "<password>"
        + "@db.example.com/postgres",
        "export APP_ENCRYPTION_KEY=$" + "APP_ENCRYPTION_KEY",
    ],
)
def test_placeholder_values_are_tolerated(tmp_path, capsys, placeholder):
    """Docs and .env.example are full of placeholders; flagging them dulls the hook."""
    path = _write(tmp_path, "notes.md", f"{placeholder}\n")

    assert check_credentials.main([path]) == 0
    assert "No credentials found" in capsys.readouterr().out


def test_a_dotenv_file_is_refused_by_name_however_clean_it_looks(tmp_path, capsys):
    """A gitignored file is one `git add -f` away from being published."""
    path = _write(tmp_path, ".env", "SUPABASE_URL=https://example.supabase.co\n")

    assert check_credentials.main([path]) == 1
    assert "must never be committed" in capsys.readouterr().out


def test_the_dotenv_template_is_allowed(tmp_path):
    path = _write(tmp_path, ".env.example", "APP_ENCRYPTION_KEY=change-me\n")
    assert check_credentials.main([path]) == 0


def test_binary_and_missing_files_are_skipped(tmp_path):
    """A PNG or a deleted path must not fail the commit (or crash the hook)."""
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")

    assert check_credentials.main([str(binary), str(tmp_path / "gone.py")]) == 0


# --- Supabase client boundaries -------------------------------------------


def test_a_data_route_reaching_for_the_shared_client_is_reported(tmp_path, capsys):
    path = _write(
        tmp_path,
        "router.py",
        "from app.core.clients.supabase import get_supabase_client\n"
        "\n"
        "def list_rows():\n"
        "    return get_supabase_client().table('customers').select('*').execute()\n",
    )

    assert check_client_boundaries.main([path]) == 1

    report = capsys.readouterr().out
    assert "app/modules/auth" in report  # says where it *is* allowed
    assert "get_current_client" in report  # ...and what to use instead


def test_creating_a_client_outside_core_is_reported(tmp_path, capsys):
    path = _write(
        tmp_path,
        "service.py",
        "from supabase import create_client\nclient = create_client(url, key)\n",
    )

    assert check_client_boundaries.main([path]) == 1
    assert "app/core/clients/supabase.py" in capsys.readouterr().out


def test_prose_about_the_clients_is_not_a_violation(tmp_path):
    """The checks are AST-based, so a comment or docstring is not a use."""
    path = _write(
        tmp_path,
        "router.py",
        '"""Never use get_supabase_client or create_client here."""\n'
        "# get_supabase_client would be wrong in a data route\n"
        "VALUE = 'get_supabase_client'\n",
    )

    assert check_client_boundaries.main([path]) == 0


def test_the_real_boundary_files_are_allowed():
    """The one factory and the auth module keep their privileges."""
    for allowed in (
        Path("app/core/clients/supabase.py"),
        Path("app/modules/auth/router.py"),
        Path("app/modules/auth/service.py"),
        Path("app/modules/auth/session.py"),
    ):
        assert check_client_boundaries.scan(allowed) == []


# --- the API prefix --------------------------------------------------------


def test_a_router_that_hard_codes_the_version_is_reported(tmp_path, capsys):
    path = _write(tmp_path, "router.py", 'router = APIRouter(prefix="/api/v1/things")\n')

    assert check_api_prefix.main([path]) == 1

    report = capsys.readouterr().out
    assert "'/api/v1/things'" in report
    assert "version=" in report  # names the registry as the place to fix it


def test_a_version_free_prefix_is_accepted(tmp_path):
    path = _write(
        tmp_path,
        "router.py",
        '# mounts under "/api/v1" courtesy of the registry\n'
        'router = APIRouter(prefix="/things", tags=["things"])\n'
        'other = APIRouter(prefix=f"{settings.api_prefix}/things")\n',
    )

    assert check_api_prefix.main([path]) == 0


# --- the inline dashboard JavaScript --------------------------------------


def test_a_syntax_error_is_reported_against_the_html_line(tmp_path, capsys):
    """Node counts lines in the extracted script; the report must not."""
    html = "<html>\n<script>\n  const ok = 1;\n  const broken = {;\n</script>\n</html>\n"
    path = _write(tmp_path, "index.html", html)

    assert check_inline_js.main([path]) == 1

    report = capsys.readouterr().out
    assert f"{path}:4" in report  # the offending line *in the file*
    assert "SyntaxError" in report


def test_a_page_without_scripts_is_not_a_silent_pass(tmp_path, capsys):
    path = _write(tmp_path, "index.html", "<html><body>no script here</body></html>\n")

    assert check_inline_js.main([path]) == 1
    assert "no inline <script> block" in capsys.readouterr().out


def test_a_missing_node_is_an_actionable_failure(monkeypatch, capsys):
    """Skipping quietly would turn this hook into a no-op on half the machines."""
    monkeypatch.setattr(check_inline_js.shutil, "which", lambda _: None)

    assert check_inline_js.main(["app/static/index.html"]) == 1
    assert "install Node.js" in capsys.readouterr().out


def test_the_real_page_is_checked():
    """The hook points at the page this repository actually ships."""
    assert (
        check_inline_js.scan_html(Path("app/static/index.html").read_text(encoding="utf-8")) == []
    )

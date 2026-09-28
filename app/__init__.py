"""PAS Backend application package.

`__version__` is the release version and the single source of truth: packaging
reads it (`[tool.setuptools.dynamic]` in pyproject.toml), the app reports it
(`Settings.app_version`, `/info`, `/health`), and `tests/test_versioning.py`
pins the three together. Bump it here and nowhere else.

This is the *software* version. The *API* version — the `/api/v1` prefix that
decides whether a client's URLs keep working — is configured separately as
`API_VERSION`; see `app/modules/registry.py`.
"""

__version__ = "0.3.0"

__all__ = ["__version__"]

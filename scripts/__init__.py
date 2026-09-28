"""Repository checks that run as pre-commit hooks and as `make` targets.

The scripts here are deliberately self-contained (standard library only) and are
imported by `tests/test_precommit_scripts.py`, which is what keeps them honest.
"""

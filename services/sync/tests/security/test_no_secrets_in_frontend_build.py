"""The frontend bundle must not contain any secret (spec 11.3, item 4).

Vite inlines every `VITE_*` variable into the JavaScript it emits. One
mistakenly-prefixed variable — `VITE_SUPABASE_SERVICE_ROLE_KEY` — would publish
a key that bypasses RLS entirely to every visitor of the GitHub Pages site, and
nothing else in the pipeline would notice.

This test needs no database and no network, so it runs on every CI job. It
checks two things:

  * the built bundle, when one exists, contains no secret value and no secret
    variable name;
  * the frontend *source* never references a secret variable name, which catches
    the mistake at review time even before a build exists.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Variable names that must never appear anywhere under apps/web.
FORBIDDEN_NAMES = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "VITE_SUPABASE_SERVICE_ROLE_KEY",
    "SERVICE_ROLE_KEY",
    "ENCRYPTION_KEY",
    "VITE_ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
)

# Environment variables whose *values* must never appear in the bundle. Checked
# only when set, so a developer with a populated .env gets the stronger check
# and CI without those secrets still gets the name-based one.
FORBIDDEN_VALUE_ENVS = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
)

SCANNED_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".html", ".json"}


def _web_dir(repo_root: Path) -> Path:
    return repo_root / "apps" / "web"


def _build_files(repo_root: Path) -> list[Path]:
    dist = _web_dir(repo_root) / "dist"
    if not dist.is_dir():
        return []
    return [p for p in dist.rglob("*") if p.is_file()]


def _source_files(repo_root: Path) -> list[Path]:
    src = _web_dir(repo_root) / "src"
    if not src.is_dir():
        return []
    return [p for p in src.rglob("*") if p.is_file() and p.suffix in SCANNED_SOURCE_SUFFIXES]


def _read(path: Path) -> str:
    """Read a build artefact as text, tolerating binary assets like fonts."""
    return path.read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize("name", FORBIDDEN_NAMES)
def test_frontend_source_never_references_a_secret_variable(repo_root: Path, name: str) -> None:
    """Catches the mistake in review, before anything is ever built."""
    offenders = [
        path.relative_to(repo_root) for path in _source_files(repo_root) if name in _read(path)
    ]
    assert not offenders, f"{name} referenced in frontend source: {offenders}"


@pytest.mark.parametrize("name", FORBIDDEN_NAMES)
def test_build_output_never_references_a_secret_variable(repo_root: Path, name: str) -> None:
    files = _build_files(repo_root)
    if not files:
        pytest.skip("no frontend build in apps/web/dist — run `npm run build` first")
    offenders = [path.relative_to(repo_root) for path in files if name in _read(path)]
    assert not offenders, f"{name} present in build output: {offenders}"


@pytest.mark.parametrize("env_name", FORBIDDEN_VALUE_ENVS)
def test_build_output_never_contains_a_secret_value(repo_root: Path, env_name: str) -> None:
    """The strongest form of the check: the literal secret, however it got there."""
    value = os.environ.get(env_name, "").strip()
    if not value:
        pytest.skip(f"{env_name} not set in this environment — value check skipped")

    files = _build_files(repo_root)
    if not files:
        pytest.skip("no frontend build in apps/web/dist — run `npm run build` first")

    offenders = [path.relative_to(repo_root) for path in files if value in _read(path)]
    # The message names the variable, never the value.
    assert not offenders, f"value of {env_name} leaked into build output: {offenders}"

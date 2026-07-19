"""Backend version stamp, shown in the dashboard header as ``be: <version>``.

Prefers the live git SHA so stale-code surprises are visible in dev. Packaged builds
(e.g. the desktop app) ship without a ``.git`` directory, so git resolution fails there —
previously that surfaced as a bare ``be: unknown``. The fallback gives those builds a
real version from the ``SCRIBE_VERSION`` env var or a bundled ``VERSION`` file instead.
"""
from __future__ import annotations

import os
import subprocess


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def git_version(repo_root: str | None = None) -> str | None:
    """The short git SHA (+``+dirty``) for repo_root, or None if git isn't available."""
    root = repo_root or _repo_root()
    try:
        sha = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
        if not sha:
            return None
        dirty = bool(subprocess.check_output(
            ["git", "-C", root, "status", "--porcelain"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip())
        return f"{sha}{'+dirty' if dirty else ''}"
    except Exception:
        return None


def fallback_version(repo_root: str | None = None) -> str:
    """Non-git version source: ``SCRIBE_VERSION`` env, then a bundled ``VERSION`` file
    at the repo/bundle root (or beside this module), else ``"unknown"``."""
    env = (os.environ.get("SCRIBE_VERSION") or "").strip()
    if env:
        return env
    root = repo_root or _repo_root()
    for candidate in (
        os.path.join(root, "VERSION"),
        os.path.join(os.path.dirname(__file__), "VERSION"),
    ):
        try:
            with open(candidate, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return "unknown"


def resolve_version(repo_root: str | None = None) -> str:
    """Git SHA when available, otherwise the non-git fallback. Never raises."""
    root = repo_root or _repo_root()
    return git_version(root) or fallback_version(root)

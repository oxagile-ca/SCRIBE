"""Loads the onboarding-generated instance config so the backend reconfigures itself
to the deployed product instead of relying on hardcoded defaults.

Resolution order for the config path:
  1. $SCRIBE_CONFIG
  2. ./instance.config.json (current working dir)
  3. ~/.scribe/instance.config.json
"""
import json
import os
from pathlib import Path


def default_config_path() -> str:
    env = os.environ.get("SCRIBE_CONFIG")
    if env:
        return env
    local = os.path.join(os.getcwd(), "instance.config.json")
    if os.path.exists(local):
        return local
    return str(Path.home() / ".scribe" / "instance.config.json")


def load_instance_config(path: str | None = None) -> dict | None:
    """Return the parsed instance config, or None if no config file exists."""
    path = path or default_config_path()
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_secrets_env(path: str | None = None) -> dict:
    """Parse a KEY=VALUE .secrets.env file into os.environ so adapters can read their
    tokens. Skips comments and blank values. Returns the dict of keys loaded."""
    path = path or os.path.join(default_config_dir(), ".secrets.env")
    if not os.path.exists(path):
        return {}
    loaded: dict = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and value:
                os.environ[key] = value
                loaded[key] = value
    return loaded


def default_config_dir() -> str:
    return os.environ.get("SCRIBE_CONFIG_DIR") or os.getcwd()


def default_skill_dir() -> str:
    return os.environ.get("SCRIBE_SKILL_DIR") or str(
        Path.home() / ".claude" / "skills" / "qa-evidence"
    )


def default_skills_root() -> str:
    """Root under which per-app skill folders (qa-evidence-<slug>) are installed."""
    return os.environ.get("SCRIBE_SKILL_DIR") or str(Path.home() / ".claude" / "skills")


def default_instances_root() -> str:
    """Repo-local root for per-app generated-skill copies (instances/<slug>/)."""
    return os.environ.get("SCRIBE_INSTANCES_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "instances"
    )

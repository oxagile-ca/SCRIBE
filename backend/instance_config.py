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


def default_config_dir() -> str:
    return os.environ.get("SCRIBE_CONFIG_DIR") or os.getcwd()


def default_skill_dir() -> str:
    return os.environ.get("SCRIBE_SKILL_DIR") or str(
        Path.home() / ".claude" / "skills" / "qa-evidence"
    )

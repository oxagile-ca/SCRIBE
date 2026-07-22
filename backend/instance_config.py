"""Loads the onboarding-generated instance config so the backend reconfigures itself
to the deployed product instead of relying on hardcoded defaults.

Resolution order for the config path:
  1. $SCRIBE_CONFIG
  2. ./instance.config.json (current working dir)
  3. ~/.scribe/instance.config.json
"""
import json
import os
import re
from pathlib import Path


def normalize_base_url(url: str) -> str:
    """Reduce a tracker base URL to its site root, dropping a pasted /browse/<KEY>
    ticket path and any trailing slash.

    Onboarding users routinely paste a full ticket link (or leave a trailing
    slash) into the base-URL field. The code builds ``{base}/rest/api/3/search/jql``
    from it, so ``.../browse/KEY/rest/...`` 302s to login and ``...net//rest/...``
    (double slash) each yield an empty board with NO error. A leading context path
    (Jira Server/DC under e.g. ``/jira``) is preserved — only the ``/browse/<KEY>``
    ticket suffix is a link, never a valid API base.
    """
    url = (url or "").strip().rstrip("/")
    url = re.sub(r"/browse/[^/]+$", "", url)
    return url.rstrip("/")


def default_config_path() -> str:
    env = os.environ.get("SCRIBE_CONFIG")
    if env:
        return env
    local = os.path.join(os.getcwd(), "instance.config.json")
    if os.path.exists(local):
        return local
    backend_local = os.path.join(_backend_dir(), "instance.config.json")
    if os.path.exists(backend_local):
        return backend_local
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


def read_secrets_file(path: str | None = None) -> dict:
    """Parse a KEY=VALUE .secrets.env into a dict WITHOUT touching os.environ.
    The merge-aware config edit path needs a side-effect-free read."""
    path = path or os.path.join(default_config_dir(), ".secrets.env")
    out: dict = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and value:
                out[key] = value
    return out


def _backend_dir() -> str:
    """Absolute path of this backend package (where .secrets.env and
    instance.config.json ship). Used as the cwd-independent fallback so launching
    the server from the wrong directory no longer silently drops LINEAR_TOKEN and
    empties the board."""
    return os.path.dirname(os.path.abspath(__file__))


def default_config_dir() -> str:
    # Was os.getcwd(): that made .secrets.env loading depend on where the process
    # happened to be launched. A restart from any other dir lost LINEAR_TOKEN, and
    # get_tickets("") fails silent -> blank board. Anchor to the backend dir instead.
    return os.environ.get("SCRIBE_CONFIG_DIR") or _backend_dir()


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

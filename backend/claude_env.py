"""Environment for spawned `claude` CLI processes.

Why this module exists: the Config Center's Anthropic API key is stored in
.secrets.env, and server.py loads .secrets.env into the BACKEND's os.environ at
startup so adapters can read their tokens. Every `claude` subprocess inherits
that environment. The CLI, seeing ANTHROPIC_API_KEY, treats the key as its auth
source and DISABLES claude.ai connectors — the org's Linear MCP vanishes and the
run dies with "Login required", which is not what is actually wrong.

Nothing in the backend reads ANTHROPIC_API_KEY (it is written by onboarding and
reported as a boolean by the Config Center), so the CLI environment scrubs it by
default. Installs that genuinely have no `claude` login — a packaged/npm install,
CI — opt back in with VERDIKT_CLAUDE_AUTH=api-key.
"""
from __future__ import annotations

import os

AUTH_MODE_ENV = "VERDIKT_CLAUDE_AUTH"
API_KEY_ENV = "ANTHROPIC_API_KEY"
#: The one value that means "authenticate the CLI with the API key instead of
#: the claude.ai login". Anything else (including unset) keeps the login.
API_KEY_MODE = "api-key"


def use_api_key_auth() -> bool:
    """True when the operator has explicitly opted into API-key auth for the CLI."""
    return os.environ.get(AUTH_MODE_ENV, "").strip().lower() == API_KEY_MODE


def claude_env() -> dict:
    """A copy of the environment safe to hand to `create_subprocess_exec`.

    Identical to os.environ except that ANTHROPIC_API_KEY is removed unless
    VERDIKT_CLAUDE_AUTH=api-key. Always a copy: the backend process keeps its
    own environment intact.
    """
    env = os.environ.copy()
    if not use_api_key_auth():
        env.pop(API_KEY_ENV, None)
    return env

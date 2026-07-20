"""The environment handed to spawned `claude` CLI processes.

ANTHROPIC_API_KEY in the environment makes the CLI treat the key as its auth
source and DISABLE claude.ai connectors — the org's Linear MCP disappears and
every QA run is blocked before it starts. Nothing in the backend reads the key,
so it is scrubbed by default and only passed through on an explicit opt-in
(VERDIKT_CLAUDE_AUTH=api-key) for installs that have no `claude` login.
"""
import os

import claude_env


def test_anthropic_key_is_scrubbed_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("VERDIKT_CLAUDE_AUTH", raising=False)
    assert "ANTHROPIC_API_KEY" not in claude_env.claude_env()


def test_every_other_var_passes_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("LINEAR_TOKEN", "lin_api_not_a_real_token")
    env = claude_env.claude_env()
    assert env["LINEAR_TOKEN"] == "lin_api_not_a_real_token"
    assert env["PATH"] == os.environ["PATH"]


def test_opt_in_keeps_the_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("VERDIKT_CLAUDE_AUTH", "api-key")
    assert claude_env.claude_env()["ANTHROPIC_API_KEY"] == "sk-ant-not-a-real-key"


def test_opt_in_tolerates_case_and_whitespace(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    for value in ("API-KEY", " api-key ", "Api-Key"):
        monkeypatch.setenv("VERDIKT_CLAUDE_AUTH", value)
        assert "ANTHROPIC_API_KEY" in claude_env.claude_env(), value


def test_unrecognized_opt_in_value_still_scrubs(monkeypatch):
    # Anything that isn't the documented opt-in means "use the claude.ai login".
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("VERDIKT_CLAUDE_AUTH", "subscription")
    assert "ANTHROPIC_API_KEY" not in claude_env.claude_env()


def test_no_key_present_is_harmless(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VERDIKT_CLAUDE_AUTH", raising=False)
    assert "ANTHROPIC_API_KEY" not in claude_env.claude_env()


def test_returns_a_copy_so_os_environ_is_untouched(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    env = claude_env.claude_env()
    env["SCRIBE_PROBE"] = "1"
    assert "SCRIBE_PROBE" not in os.environ
    # scrubbing the child's env must not unset the key for the backend itself
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-not-a-real-key"

"""Tests for the instance-config loader."""
import json
import os

from instance_config import load_instance_config, load_secrets_env


def test_load_instance_config_reads_json(tmp_path):
    p = tmp_path / "instance.config.json"
    p.write_text(
        json.dumps(
            {"productName": "Acme CMS", "issueTracker": {"type": "jira", "projects": ["ACME"]}}
        ),
        encoding="utf-8",
    )
    cfg = load_instance_config(str(p))
    assert cfg["productName"] == "Acme CMS"
    assert cfg["issueTracker"]["projects"] == ["ACME"]


def test_load_instance_config_missing_returns_none(tmp_path):
    assert load_instance_config(str(tmp_path / "nope.json")) is None


def test_load_secrets_env_sets_environ(tmp_path, monkeypatch):
    p = tmp_path / ".secrets.env"
    p.write_text("LINEAR_TOKEN=lin_api_abc\n# a comment\n\nEMPTY=\nJIRA_TOKEN=jt\n", encoding="utf-8")
    monkeypatch.delenv("LINEAR_TOKEN", raising=False)
    result = load_secrets_env(str(p))
    assert result["LINEAR_TOKEN"] == "lin_api_abc"
    assert result["JIRA_TOKEN"] == "jt"
    assert "EMPTY" not in result  # blank value skipped
    assert os.environ["LINEAR_TOKEN"] == "lin_api_abc"


def test_load_secrets_env_missing_returns_empty(tmp_path):
    assert load_secrets_env(str(tmp_path / "nope.env")) == {}

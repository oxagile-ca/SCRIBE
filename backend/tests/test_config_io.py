import json
import os
import instance_config
import onboarding


def test_read_secrets_file_parses_without_touching_environ(tmp_path, monkeypatch):
    p = tmp_path / ".secrets.env"
    p.write_text("# comment\nLINEAR_TOKEN=abc123\nEMPTY=\nGITHUB_TOKEN = gh_x \n", encoding="utf-8")
    monkeypatch.delenv("LINEAR_TOKEN", raising=False)
    out = instance_config.read_secrets_file(str(p))
    assert out == {"LINEAR_TOKEN": "abc123", "GITHUB_TOKEN": "gh_x"}
    assert "LINEAR_TOKEN" not in os.environ  # did NOT mutate environ


def test_read_secrets_file_missing_returns_empty(tmp_path):
    assert instance_config.read_secrets_file(str(tmp_path / "nope.env")) == {}


def test_write_config_and_secrets_writes_both(tmp_path):
    cfg = {"productName": "X", "issueTracker": {"token": "${secret:LINEAR_TOKEN}"}}
    secrets = {"LINEAR_TOKEN": "abc", "GITHUB_TOKEN": "gh"}
    paths = onboarding.write_config_and_secrets(cfg, secrets, str(tmp_path))
    assert json.load(open(paths["config"], encoding="utf-8")) == cfg
    body = open(paths["secrets"], encoding="utf-8").read()
    assert "LINEAR_TOKEN=abc" in body and "GITHUB_TOKEN=gh" in body
    # config file must NOT contain a real secret value
    assert "abc" not in open(paths["config"], encoding="utf-8").read()

"""Tests for the instance-config loader."""
import json

from instance_config import load_instance_config


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

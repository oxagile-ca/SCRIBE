import json
import os
import instance_config
import onboarding
import config_io


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


_CFG = {
    "orgName": "Acme", "productName": "Beeventory", "productType": "webapp",
    "description": "d", "urls": ["https://x"], "appSlug": "beeventory",
    "skillCommand": "/qa-evidence-beeventory",
    "environments": {"mode": "static", "staticUrls": ["https://x"],
                     "testAuth": {"required": True, "loginUrl": "u", "username": "admin",
                                  "password": "${secret:TEST_LOGIN_PASSWORD}", "notes": ""}},
    "issueTracker": {"type": "linear", "baseUrl": "b", "projects": ["INV"], "email": "e",
                     "token": "${secret:LINEAR_TOKEN}", "access": {"read": True, "write": True}},
    "vcs": {"type": "github", "org": "o", "repos": ["a", "b"],
            "token": "${secret:GITHUB_TOKEN}", "access": {"read": True, "write": False}},
    "publish": {"jiraComment": True, "prComment": True, "slackWebhook": "",
                "confluence": {"baseUrl": "", "spaceKey": "", "parentPage": "", "token": ""}},
    "knowledge": {"provider": "none", "link": "", "token": "", "access": {"read": True, "write": False}},
    "api": {"baseUrl": "https://api", "postmanCollectionPath": "/p.json"},
}
_SECRETS = {"LINEAR_TOKEN": "lt", "GITHUB_TOKEN": "gh", "TEST_LOGIN_PASSWORD": "pw"}


def test_config_to_answers_blanks_secrets_and_shapes_company():
    a = config_io.config_to_answers(_CFG)
    assert a["company"]["productName"] == "Beeventory"
    assert a["company"]["orgName"] == "Acme"
    assert a["issueTracker"]["token"] == ""           # secret blanked
    assert a["issueTracker"]["projects"] == ["INV"]   # non-secret preserved
    assert a["vcs"]["repos"] == ["a", "b"]
    assert a["environments"]["testAuth"]["password"] == ""
    assert a["api"]["postmanCollectionPath"] == "/p.json"
    assert a["issueTracker"]["statusMapping"]["ready_for_qa"]  # defaulted
    assert "criticalFlows" in a["productQA"]          # present but empty
    # never leaks a real secret ref into a blanked field
    assert "${secret" not in a["issueTracker"]["token"]


def test_secrets_set_map_reports_presence():
    m = config_io.secrets_set_map(_CFG, _SECRETS)
    assert m["issueTracker.token"] is True
    assert m["vcs.token"] is True
    assert m["environments.testAuth.password"] is True
    assert m["publish.slackWebhook"] is False
    assert m["anthropicKey"] is False


def test_merge_blank_token_keeps_existing_ref_and_value():
    answers = config_io.config_to_answers(_CFG)  # all secrets blank
    cfg, secrets = config_io.merge_and_build(answers, _CFG, _SECRETS)
    assert cfg["issueTracker"]["token"] == "${secret:LINEAR_TOKEN}"  # ref restored
    assert secrets["LINEAR_TOKEN"] == "lt"                            # value kept
    assert cfg["vcs"]["token"] == "${secret:GITHUB_TOKEN}"
    # config never contains a real secret value
    assert "lt" not in json_str(cfg) and "gh" not in json_str(cfg)


def test_merge_new_token_replaces():
    answers = config_io.config_to_answers(_CFG)
    answers["issueTracker"]["token"] = "NEWLT"
    cfg, secrets = config_io.merge_and_build(answers, _CFG, _SECRETS)
    assert secrets["LINEAR_TOKEN"] == "NEWLT"
    assert cfg["issueTracker"]["token"] == "${secret:LINEAR_TOKEN}"


def test_merge_preserves_identity_on_productname_edit():
    answers = config_io.config_to_answers(_CFG)
    answers["company"]["productName"] = "Renamed Product"
    cfg, _ = config_io.merge_and_build(answers, _CFG, _SECRETS)
    assert cfg["appSlug"] == "beeventory"               # NOT recomputed
    assert cfg["skillCommand"] == "/qa-evidence-beeventory"


def json_str(d):
    return json.dumps(d)

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


def test_config_to_answers_defaults_statusmapping_per_tracker():
    """A Linear config with no statusMapping must default to the Linear status
    names (so the QA queue matches the live 'Ready for Testing' status), not the
    generic Jira 'Ready for QA' placeholder."""
    a = config_io.config_to_answers(_CFG)  # _CFG: type=linear, no statusMapping
    rfq = a["issueTracker"]["statusMapping"]["ready_for_qa"]
    assert any(s.strip().lower() == "ready for testing" for s in rfq), rfq
    assert rfq != ["Ready for QA"], "still the generic Jira placeholder"


def test_secrets_set_map_reports_presence():
    m = config_io.secrets_set_map(_CFG, _SECRETS)
    assert m["issueTracker.token"] is True
    assert m["vcs.token"] is True
    assert m["environments.testAuth.password"] is True
    assert m["publish.slackWebhook"] is False
    assert m["knowledge.token"] is False
    assert m["anthropicKey"] is False


def test_merge_blank_token_keeps_existing_ref_and_value():
    answers = config_io.config_to_answers(_CFG)  # all secrets blank
    cfg, secrets = config_io.merge_and_build(answers, _CFG, _SECRETS)
    assert cfg["issueTracker"]["token"] == "${secret:LINEAR_TOKEN}"  # ref restored
    assert secrets["LINEAR_TOKEN"] == "lt"                            # value kept
    assert cfg["vcs"]["token"] == "${secret:GITHUB_TOKEN}"
    assert cfg["environments"]["testAuth"]["password"] == "${secret:TEST_LOGIN_PASSWORD}"
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


def test_config_to_answers_blanks_publish_slack_ref():
    cfg = dict(_CFG)
    cfg["publish"] = {"slackWebhook": "${secret:SLACK_WEBHOOK}",
                      "confluence": {"baseUrl": "", "spaceKey": "", "parentPage": "",
                                     "token": "${secret:CONFLUENCE_TOKEN}"}}
    a = config_io.config_to_answers(cfg)
    assert a["publish"]["slackWebhook"] == ""
    assert a["publish"]["confluence"]["token"] == ""


def json_str(d):
    return json.dumps(d)


# --- Application Profile: productQA / qaTargets round-trip + skill staleness ---

_PQA = {
    "criticalFlows": ["checkout", "refund with manager cap"],
    "saveSemantics": "Save persists a draft order.",
    "publishSemantics": "Publish commits the order.",
    "keyPages": [{"name": "Orders", "route": "/orders"}],
    "riskAreas": ["refund float rounding", "postal unicode loss"],
    "alwaysCheck": ["audit trail written"],
}
_QT = {"seedEntities": ["order", "product"], "classifyRules": [{"match": "refund", "type": "refund"}]}


def _answers_with_qa() -> dict:
    return {
        "company": {"orgName": "Northstar", "productName": "Northstar Commerce",
                    "productType": "webapp", "description": "e-commerce console", "urls": ["https://x"]},
        "environments": {"mode": "deployed",
                         "testAuth": {"required": True, "loginUrl": "u", "username": "m",
                                      "password": "pw", "notes": ""}},
        "issueTracker": {"type": "linear", "baseUrl": "b", "projects": ["NOR"], "email": "e",
                         "token": "lt", "access": {"read": True, "write": True}},
        "vcs": {"type": "github", "org": "o", "repos": ["r"], "token": "gh",
                "access": {"read": True, "write": True}},
        "publish": {}, "knowledge": {"provider": "none"},
        "api": {"baseUrl": "https://api"},
        "productQA": _PQA, "qaTargets": _QT,
    }


def test_product_qa_and_qatargets_round_trip():
    cfg, _ = onboarding.build_instance_config(_answers_with_qa())
    assert cfg["productQA"]["riskAreas"] == _PQA["riskAreas"]     # persisted (was dropped before)
    assert cfg["qaTargets"] == _QT
    a = config_io.config_to_answers(cfg)
    assert a["productQA"]["criticalFlows"] == _PQA["criticalFlows"]   # hydrated back
    assert a["productQA"]["keyPages"] == [{"name": "Orders", "route": "/orders"}]
    assert a["qaTargets"] == _QT                                      # no longer silently dropped


def test_lean_config_yields_empty_productqa_shape():
    cfg, _ = onboarding.build_instance_config({"company": {"productName": "X"},
                                               "issueTracker": {"type": "jira"}, "vcs": {"type": "github"}})
    assert "productQA" not in cfg                                     # lean: not written when empty
    a = config_io.config_to_answers(cfg)
    assert a["productQA"] == {"criticalFlows": [], "saveSemantics": "", "publishSemantics": "",
                              "keyPages": [], "riskAreas": [], "alwaysCheck": []}
    assert "qaTargets" not in a


def test_merge_preserves_productqa_and_qatargets():
    cfg, _ = onboarding.build_instance_config(_answers_with_qa())
    answers = config_io.config_to_answers(cfg)               # simulate loading into the editor
    merged, _secrets = config_io.merge_and_build(answers, cfg, {"LINEAR_TOKEN": "lt", "GITHUB_TOKEN": "gh"})
    assert merged["productQA"]["riskAreas"] == _PQA["riskAreas"]
    assert merged["qaTargets"] == _QT


def test_skill_signature_changes_on_riskarea_edit_not_on_email():
    cfg, _ = onboarding.build_instance_config(_answers_with_qa())
    base = config_io.config_skill_signature(cfg)
    cfg_email = json.loads(json.dumps(cfg)); cfg_email["issueTracker"]["email"] = "other@x"
    assert config_io.config_skill_signature(cfg_email) == base       # tracker email is not a skill input
    cfg_risk = json.loads(json.dumps(cfg)); cfg_risk["productQA"]["riskAreas"] = ["totally different risk"]
    assert config_io.config_skill_signature(cfg_risk) != base        # productQA edit => stale


def test_skill_signature_ignores_qatargets_edit():
    cfg, _ = onboarding.build_instance_config(_answers_with_qa())
    base = config_io.config_skill_signature(cfg)
    cfg2 = json.loads(json.dumps(cfg)); cfg2["qaTargets"] = {"seedEntities": ["different"]}
    assert config_io.config_skill_signature(cfg2) == base            # qaTargets is a runtime input, no rebuild

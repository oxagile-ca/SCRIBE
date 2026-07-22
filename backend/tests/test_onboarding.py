"""Tests for the onboarding generator (config + skill + patterns)."""
import json
import os

import yaml

from onboarding import (
    app_slug,
    build_instance_config,
    build_patterns,
    render_skill,
    run_onboarding,
    validate_answers,
    write_outputs,
)


def sample_answers():
    return {
        "company": {
            "orgName": "Acme Inc",
            "productName": "Acme CMS",
            "description": "A headless CMS for publishers.",
            "productType": "cms",
            "urls": ["https://acme.example.com"],
        },
        "environments": {
            "mode": "static",
            "staticUrls": ["https://staging.acme.example.com"],
            "testAuth": {
                "required": True,
                "loginUrl": "https://staging.acme.example.com/login",
                "username": "qa@acme.example.com",
                "password": "s3cret-pw",
                "notes": "SSO disabled in staging",
            },
        },
        "issueTracker": {
            "type": "jira",
            "baseUrl": "https://acme.atlassian.net",
            "projects": ["ACME"],
            "email": "qa@acme.example.com",
            "token": "jira-tok",
            "access": {"read": True, "write": True},
        },
        "vcs": {
            "type": "github",
            "org": "acme",
            "repos": ["acme/cms"],
            "token": "gh-tok",
            "access": {"read": True, "write": False},
        },
        "publish": {
            "jiraComment": True,
            "prComment": True,
            "slackWebhook": "https://hooks.slack.com/x",
            "confluence": {
                "baseUrl": "https://acme.atlassian.net/wiki",
                "spaceKey": "QA",
                "parentPage": "Evidence",
                "token": "conf-tok",
            },
        },
        "productQA": {
            "criticalFlows": ["Create and publish an article"],
            "saveSemantics": "Click Save, wait for the 'Saved' toast.",
            "publishSemantics": "Click Publish; status flips to Published.",
            "keyPages": [{"name": "Article editor", "route": "/edit/{id}"}],
            "riskAreas": ["Rich-text field loses formatting on reload"],
            "alwaysCheck": ["No console errors"],
        },
        "knowledge": {
            "provider": "notion",
            "link": "https://www.notion.so/acme/Product-Docs-abc123",
            "token": "notion-tok",
            "access": {"read": True, "write": False},
        },
        "anthropicKey": "sk-ant-xxx",
    }


def test_app_slug_normalizes_product_name():
    assert app_slug("Beeventory ") == "beeventory"
    assert app_slug("Acme CMS") == "acme-cms"
    assert app_slug("My App!! 2.0") == "my-app-2-0"
    assert app_slug("") == "app"


def test_build_instance_config_sets_app_slug_and_skill_command():
    config, _ = build_instance_config(sample_answers())  # productName "Acme CMS"
    assert config["appSlug"] == "acme-cms"
    assert config["skillCommand"] == "/qa-evidence-acme-cms"


def test_build_instance_config_normalizes_a_pasted_ticket_base_url():
    answers = sample_answers()
    answers["issueTracker"]["baseUrl"] = "https://x.atlassian.net/browse/ABC-1"
    config, _ = build_instance_config(answers)
    assert config["issueTracker"]["baseUrl"] == "https://x.atlassian.net"


def test_build_instance_config_passes_status_mapping_through():
    answers = sample_answers()
    answers["issueTracker"]["statusMapping"] = {
        "ready_for_qa": ["Ready for testing"],
        "in_qa": ["In QA"],
    }
    config, _ = build_instance_config(answers)
    assert config["issueTracker"]["statusMapping"]["ready_for_qa"] == ["Ready for testing"]


def test_build_instance_config_splits_secrets_out_of_config():
    config, secrets = build_instance_config(sample_answers())

    # non-secret config keeps identifying product info
    assert config["productName"] == "Acme CMS"
    assert config["issueTracker"]["type"] == "jira"
    assert config["issueTracker"]["projects"] == ["ACME"]
    assert config["vcs"]["type"] == "github"

    # access flags are persisted on each connection
    assert config["issueTracker"]["access"] == {"read": True, "write": True}
    assert config["vcs"]["access"] == {"read": True, "write": False}
    assert config["knowledge"]["access"] == {"read": True, "write": False}
    assert config["knowledge"]["provider"] == "notion"
    assert config["knowledge"]["link"].endswith("Product-Docs-abc123")

    # NO raw secret value appears anywhere in the non-secret config
    blob = json.dumps(config)
    for leaked in ("jira-tok", "gh-tok", "conf-tok", "notion-tok", "s3cret-pw", "sk-ant-xxx"):
        assert leaked not in blob, f"{leaked} leaked into instance.config"

    # secrets are referenced as ${secret:KEY} so the registry can resolve them
    assert config["issueTracker"]["token"] == "${secret:JIRA_TOKEN}"
    assert config["knowledge"]["token"] == "${secret:NOTION_TOKEN}"

    # the secret store carries the real values, keyed for the registry
    assert secrets["JIRA_TOKEN"] == "jira-tok"
    assert secrets["GITHUB_TOKEN"] == "gh-tok"
    assert secrets["CONFLUENCE_TOKEN"] == "conf-tok"
    assert secrets["NOTION_TOKEN"] == "notion-tok"
    assert secrets["TEST_LOGIN_PASSWORD"] == "s3cret-pw"
    assert secrets["ANTHROPIC_API_KEY"] == "sk-ant-xxx"


BASE_SKILL = """# /qa-evidence

Intro text that must survive.

<!-- PRODUCT_CONTEXT -->

## Phase 0: Validate
Do stuff.
"""


def test_render_skill_injects_product_context_at_marker():
    out = render_skill(sample_answers(), BASE_SKILL)

    # marker replaced, base content preserved
    assert "<!-- PRODUCT_CONTEXT -->" not in out
    assert "Intro text that must survive." in out
    assert "## Phase 0: Validate" in out

    # YAML frontmatter so Claude Code registers the per-app skill + slash command
    assert out.startswith("---\n")
    assert "name: qa-evidence-acme-cms" in out
    assert "/qa-evidence-acme-cms" in out
    # generated product context present
    assert "Product Context" in out
    assert "Acme CMS" in out
    assert "Create and publish an article" in out
    assert "Click Save" in out  # save semantics
    assert "Product-Docs-abc123" in out  # knowledge source link


def test_render_skill_without_marker_prepends_context():
    out = render_skill(sample_answers(), "# Skill\nNo marker present.\n")
    assert "No marker present." in out
    assert "Acme CMS" in out
    assert "Product Context" in out


def test_build_patterns_makes_one_rule_per_risk_area_and_baseline():
    patterns = build_patterns(sample_answers())

    rules = patterns["rules"]
    assert len(rules) == 1  # one risk area in sample
    rule = rules[0]
    assert rule["id"].startswith("PAT-")
    assert "Rich-text" in rule["name"]
    assert rule["triggers"]["keywords"]  # salient keywords extracted, non-empty
    assert rule["inject_tcs"][0]["title"]  # becomes a test case

    # alwaysCheck items become the always-on baseline, consumable by qa_patterns
    assert patterns["baseline_always_on"] == ["No console errors"]


def test_build_patterns_handles_no_risks_or_checks():
    answers = sample_answers()
    answers["productQA"]["riskAreas"] = []
    answers["productQA"]["alwaysCheck"] = []
    patterns = build_patterns(answers)
    assert patterns["rules"] == []
    assert patterns["baseline_always_on"] == []


def test_write_outputs_writes_all_files_and_is_idempotent(tmp_path):
    answers = sample_answers()
    config, secrets = build_instance_config(answers)
    skill = render_skill(answers, BASE_SKILL)
    patterns = build_patterns(answers)

    config_dir = tmp_path / "cfg"
    skill_dir = tmp_path / "skill"
    paths = write_outputs(
        config, secrets, skill, patterns,
        config_dir=str(config_dir), skill_dir=str(skill_dir),
    )

    for key in ("config", "secrets", "skill", "patterns"):
        assert os.path.exists(paths[key]), f"{key} not written"

    cfg = json.loads(open(paths["config"], encoding="utf-8").read())
    assert cfg["productName"] == "Acme CMS"

    secrets_text = open(paths["secrets"], encoding="utf-8").read()
    assert "JIRA_TOKEN=jira-tok" in secrets_text
    assert "ANTHROPIC_API_KEY=sk-ant-xxx" in secrets_text

    pat = yaml.safe_load(open(paths["patterns"], encoding="utf-8").read())
    assert pat["rules"][0]["id"] == "PAT-001"

    assert "Product Context" in open(paths["skill"], encoding="utf-8").read()

    # idempotent: re-run overwrites cleanly, same paths
    paths2 = write_outputs(
        config, secrets, skill, patterns,
        config_dir=str(config_dir), skill_dir=str(skill_dir),
    )
    assert paths2 == paths


def test_validate_answers_passes_for_complete_sample():
    assert validate_answers(sample_answers()) == []


def test_validate_answers_flags_missing_required_fields():
    errors = validate_answers(
        {"company": {}, "issueTracker": {}, "vcs": {}, "environments": {}}
    )
    assert any("productName" in e for e in errors)
    assert any("issue tracker" in e.lower() for e in errors)
    assert any("vcs" in e.lower() for e in errors)
    assert any("environment" in e.lower() or "mode" in e.lower() for e in errors)


def test_validate_answers_static_mode_requires_a_url():
    answers = sample_answers()
    answers["environments"] = {"mode": "static", "staticUrls": []}
    errors = validate_answers(answers)
    assert any("url" in e.lower() for e in errors)


def test_validate_answers_script_mode_requires_commands():
    answers = sample_answers()
    answers["environments"] = {"mode": "script", "buildCmd": "", "deployCmd": ""}
    errors = validate_answers(answers)
    assert any("build" in e.lower() or "deploy" in e.lower() for e in errors)


def test_run_onboarding_writes_all_artifacts_and_summarizes(tmp_path):
    base = tmp_path / "base.md"
    base.write_text(BASE_SKILL, encoding="utf-8")

    result = run_onboarding(
        sample_answers(),
        config_dir=str(tmp_path / "cfg"),
        skills_root=str(tmp_path / "skills"),
        repo_instances_root=str(tmp_path / "instances"),
        base_skill_path=str(base),
    )

    assert os.path.exists(result["paths"]["config"])
    assert os.path.exists(result["paths"]["secrets"])

    # skill installed to a DEDICATED per-app folder (no generic-name collision)
    install = os.path.join(str(tmp_path / "skills"), "qa-evidence-acme-cms", "SKILL.md")
    assert os.path.exists(install)
    assert result["paths"]["skill"] == install
    # and a versioned repo copy under instances/<app>/
    repo_skill = os.path.join(str(tmp_path / "instances"), "acme-cms", "SKILL.md")
    assert os.path.exists(repo_skill)
    assert os.path.exists(os.path.join(str(tmp_path / "instances"), "acme-cms", "patterns.yml"))

    summary = result["summary"]
    assert summary["productName"] == "Acme CMS"
    assert summary["appSlug"] == "acme-cms"
    assert summary["skillCommand"] == "/qa-evidence-acme-cms"
    assert summary["envMode"] == "static"
    assert summary["patternRules"] == 1
    assert "Acme CMS" in open(install, encoding="utf-8").read()

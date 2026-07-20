"""rebuild_skill regenerates the skill bundle from persisted config, re-stamps skillMeta,
and never touches secrets — the backend behind the Application Profile's 'Rebuild skill'."""
import json

import onboarding
import config_io


def _answers() -> dict:
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
        "productQA": {
            "criticalFlows": ["checkout"], "saveSemantics": "", "publishSemantics": "",
            "keyPages": [], "riskAreas": ["refund float rounding"], "alwaysCheck": ["audit trail written"],
        },
    }


def test_rebuild_skill_writes_artifacts_and_stamps_meta_without_touching_secrets(tmp_path):
    cfg_dir = tmp_path / "cfg"; skills = tmp_path / "skills"; repo = tmp_path / "repo"
    cfg_dir.mkdir()
    cfg, secrets = onboarding.build_instance_config(_answers())
    onboarding.write_config_and_secrets(cfg, secrets, str(cfg_dir))
    secrets_before = (cfg_dir / ".secrets.env").read_text(encoding="utf-8")
    slug = cfg["appSlug"]

    res = onboarding.rebuild_skill(config_dir=str(cfg_dir), skills_root=str(skills),
                                   repo_instances_root=str(repo))

    inst = skills / f"qa-evidence-{slug}"
    assert (inst / "SKILL.md").exists() and (inst / "patterns.yml").exists()
    assert (repo / slug / "SKILL.md").exists()
    assert (inst / "scribe-live-api.js").exists()          # api.baseUrl set -> helper shipped
    skill_txt = (inst / "SKILL.md").read_text(encoding="utf-8")
    assert "refund float rounding" in skill_txt            # edited knowledge reached the skill

    written = json.loads((cfg_dir / "instance.config.json").read_text(encoding="utf-8"))
    assert written["skillMeta"]["inputsHash"] == config_io.config_skill_signature(written)
    assert written["skillMeta"]["builtAt"] and res["builtAt"] == written["skillMeta"]["builtAt"]
    assert res["patternRules"] == 1                        # one risk area -> one seeded rule

    # a rebuild must never rewrite secrets
    assert (cfg_dir / ".secrets.env").read_text(encoding="utf-8") == secrets_before


def test_run_onboarding_starts_not_stale(tmp_path):
    cfg_dir = tmp_path / "cfg"
    onboarding.run_onboarding(_answers(), config_dir=str(cfg_dir),
                              skills_root=str(tmp_path / "skills"),
                              repo_instances_root=str(tmp_path / "repo"))
    written = json.loads((cfg_dir / "instance.config.json").read_text(encoding="utf-8"))
    # freshly onboarded: stamped hash equals the recomputed one -> GET reports skillStale=False
    assert written["skillMeta"]["inputsHash"] == config_io.config_skill_signature(written)


def test_edit_then_rebuild_clears_staleness(tmp_path):
    cfg_dir = tmp_path / "cfg"; skills = tmp_path / "skills"; repo = tmp_path / "repo"
    cfg_dir.mkdir()
    cfg, secrets = onboarding.build_instance_config(_answers())
    cfg["skillMeta"] = {"builtAt": "2026-01-01T00:00:00+00:00",
                        "inputsHash": config_io.config_skill_signature(cfg)}
    onboarding.write_config_and_secrets(cfg, secrets, str(cfg_dir))

    # simulate a Product QA edit landing in the persisted config (as PUT /api/config would)
    edited = json.loads((cfg_dir / "instance.config.json").read_text(encoding="utf-8"))
    edited["productQA"]["riskAreas"] = ["a brand new risk area"]
    onboarding.write_config_and_secrets(edited, secrets, str(cfg_dir))
    stored = json.loads((cfg_dir / "instance.config.json").read_text(encoding="utf-8"))
    assert stored["skillMeta"]["inputsHash"] != config_io.config_skill_signature(stored)  # now stale

    onboarding.rebuild_skill(config_dir=str(cfg_dir), skills_root=str(skills), repo_instances_root=str(repo))
    after = json.loads((cfg_dir / "instance.config.json").read_text(encoding="utf-8"))
    assert after["skillMeta"]["inputsHash"] == config_io.config_skill_signature(after)     # cleared

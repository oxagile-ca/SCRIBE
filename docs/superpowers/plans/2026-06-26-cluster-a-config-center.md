# Cluster A — Config Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Settings (Config Center) screen to view the saved config without secrets (#1), edit it (#2), upload a Postman collection (#3), manage multiple GitHub repo URLs (#5), and toggle per-integration read/write permissions (#11) — without re-walking onboarding and without a backend restart.

**Architecture:** Reuse the onboarding machinery behind a merge-aware edit path. A new `config_io.py` maps the on-disk config to the wizard's `OnboardingAnswers` form shape (secrets blanked), merges edits back (`blank secret = keep`, preserving `appSlug`/`skillCommand`), and the existing `build_instance_config` + a factored-out `write_config_and_secrets` persist them with a hot `os.environ` reload. The frontend adds a single-page `Settings` modal reusing the wizard's field components.

**Tech Stack:** Python (FastAPI, pytest), React 18 + TypeScript + Vite (plain CSS), httpx already present; `python-multipart` added for file upload.

## Global Constraints

- **Python interpreter / tests:** from `C:\Users\ankit\SCRIBE\backend` run `..\.venv\Scripts\python.exe -m pytest <file> -v`. Never the bare `python3` (Store shim).
- **Frontend has no test runner:** verify with `npm run build` (tsc) from `C:\Users\ankit\SCRIBE\frontend`; manual check otherwise.
- **Secrets never land in `instance.config.json`** — only `${secret:KEY}` refs. The on-disk config is already secret-free.
- **Blank secret field = keep** the existing secret; non-blank = replace (written to `.secrets.env`, then `load_secrets_env()` hot-reloads `os.environ` — no restart).
- **Preserve identity on edit:** carry `appSlug` and `skillCommand` from the existing config (do not recompute from `productName`).
- **Validation:** reuse `onboarding.validate_answers` verbatim — it is already token-agnostic (only structural rules). Do NOT add a separate validator.
- **`productQA` is omitted** from the Settings screen (not persisted to config; `build_instance_config` doesn't include it).
- **Postman upload** stores the file + sets `api.postmanCollectionPath` + re-parses for an endpoint count; it does NOT rewrite `SKILL.md`.
- **`python-multipart` required** for `UploadFile`; add it to `requirements.txt` and install it in the venv.
- **Branch:** `feat/cluster-a-config-center` (stacked on Cluster C). Commit after every task.
- **Stream/endpoint style:** mirror existing handlers; JSON via `Dict[str, Any]` body like `POST /api/onboarding`.

---

## File structure

**New backend:** `backend/config_io.py` (config↔answers mapping + merge). **Modified backend:** `backend/instance_config.py` (+`read_secrets_file`), `backend/onboarding.py` (+`write_config_and_secrets`, +`save_postman_collection`, refactor `write_outputs`), `backend/server.py` (+3 endpoints, imports), `backend/requirements.txt` (+python-multipart).
**New frontend:** `frontend/src/components/Onboarding/fields.tsx` (extracted field components), `frontend/src/components/Settings.tsx`. **Modified frontend:** `OnboardingWizard.tsx` (import extracted fields), `api.ts` (+3 functions), `components/TopBar.tsx` (+⚙), `App.tsx` (+modal state).
**New tests:** `backend/tests/test_config_io.py`, `backend/tests/test_config_endpoints.py`, `backend/tests/test_postman_upload.py`.

---

## Task 1: Backend primitives — `read_secrets_file` + `write_config_and_secrets`

**Files:**
- Modify: `backend/instance_config.py` (add `read_secrets_file`)
- Modify: `backend/onboarding.py` (add `write_config_and_secrets`, refactor `write_outputs` to use it)
- Test: `backend/tests/test_config_io.py` (primitives portion)

**Interfaces:**
- Produces: `instance_config.read_secrets_file(path: str | None = None) -> dict` (non-mutating); `onboarding.write_config_and_secrets(config: dict, secrets: dict, config_dir: str) -> dict` (returns `{"config": path, "secrets": path}`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_config_io.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_config_io.py -v`
Expected: FAIL — `AttributeError: module 'instance_config' has no attribute 'read_secrets_file'`.

- [ ] **Step 3: Add `read_secrets_file` to `instance_config.py`**

In `backend/instance_config.py`, add after `load_secrets_env` (after line 53):
```python
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
```

- [ ] **Step 4: Add `write_config_and_secrets` to `onboarding.py` and refactor `write_outputs`**

In `backend/onboarding.py`, add this function just above `write_outputs` (before line 427):
```python
def write_config_and_secrets(config: dict, secrets: dict, config_dir: str) -> dict:
    """Write instance.config.json + .secrets.env to config_dir; return their paths.
    Shared by write_outputs (full onboarding) and the Config Center edit path."""
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "instance.config.json")
    secrets_path = os.path.join(config_dir, ".secrets.env")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    with open(secrets_path, "w", encoding="utf-8") as fh:
        for key, value in secrets.items():
            fh.write(f"{key}={value}\n")
    return {"config": config_path, "secrets": secrets_path}
```

Then in `write_outputs`, replace the two write blocks (currently lines 462-467):
```python
    with open(paths["config"], "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)

    with open(paths["secrets"], "w", encoding="utf-8") as fh:
        for key, value in secrets.items():
            fh.write(f"{key}={value}\n")
```
with a single call (keeps `paths["config"]`/`paths["secrets"]` identical since `write_config_and_secrets` derives the same names from `config_dir`):
```python
    write_config_and_secrets(config, secrets, config_dir)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_config_io.py -v`
Expected: 3 passed.

- [ ] **Step 6: Sanity-check onboarding still imports + writes**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -c "import onboarding; print('ok')"`
Expected: `ok` (the refactored `write_outputs` parses).

- [ ] **Step 7: Commit**

```bash
git add backend/instance_config.py backend/onboarding.py backend/tests/test_config_io.py
git commit -m "feat(config): read_secrets_file + write_config_and_secrets primitive"
```

---

## Task 2: `config_io.py` — config↔answers mapping + merge (the crux)

**Files:**
- Create: `backend/config_io.py`
- Test: `backend/tests/test_config_io.py` (extend)

**Interfaces:**
- Consumes: `onboarding.build_instance_config`, `onboarding._ref`, `onboarding.ISSUE_SECRET_KEY/VCS_SECRET_KEY/KNOWLEDGE_SECRET_KEY`.
- Produces:
  - `config_to_answers(config: dict) -> dict` — OnboardingAnswers-shaped, secrets blanked, productQA empty, statusMapping defaulted.
  - `secrets_set_map(config: dict, secrets: dict) -> dict` — `{field_id: bool}` incl `"anthropicKey"`.
  - `merge_and_build(answers: dict, existing_config: dict, existing_secrets: dict) -> tuple[dict, dict]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_config_io.py`:
```python
import config_io

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
    import json
    return json.dumps(d)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_config_io.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config_io'`.

- [ ] **Step 3: Write `config_io.py`**

Create `backend/config_io.py`:
```python
"""Map the on-disk instance config to/from the onboarding form shape, and merge edits.

The Config Center edits config via the same OnboardingAnswers shape the wizard uses.
On the way in we blank secrets (the on-disk config holds only ${secret:} refs anyway);
on the way out we keep blanked secrets (blank = keep) and preserve the onboarded
appSlug/skillCommand so an edit never renames the generated skill.
"""
import copy

from onboarding import (
    build_instance_config, _ref,
    ISSUE_SECRET_KEY, VCS_SECRET_KEY, KNOWLEDGE_SECRET_KEY,
)


def _secret_specs(config: dict) -> list[tuple[str, tuple, str]]:
    """(field_id, path, secret_key) for every secret field, key resolved vs this config."""
    it = config.get("issueTracker") or {}
    vcs = config.get("vcs") or {}
    kn = config.get("knowledge") or {}
    return [
        ("issueTracker.token", ("issueTracker", "token"), ISSUE_SECRET_KEY.get(it.get("type"), "ISSUE_TOKEN")),
        ("vcs.token", ("vcs", "token"), VCS_SECRET_KEY.get(vcs.get("type"), "VCS_TOKEN")),
        ("knowledge.token", ("knowledge", "token"), KNOWLEDGE_SECRET_KEY.get(kn.get("provider"), "KNOWLEDGE_TOKEN")),
        ("environments.testAuth.password", ("environments", "testAuth", "password"), "TEST_LOGIN_PASSWORD"),
        ("publish.slackWebhook", ("publish", "slackWebhook"), "SLACK_WEBHOOK"),
        ("publish.confluence.token", ("publish", "confluence", "token"), "CONFLUENCE_TOKEN"),
    ]


def _dig(d: dict, path: tuple):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _put(d: dict, path: tuple, value) -> None:
    cur = d
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[path[-1]] = value


def config_to_answers(config: dict) -> dict:
    """Reshape on-disk config into the OnboardingAnswers form shape, secrets blanked."""
    cfg = copy.deepcopy(config or {})
    answers = {
        "company": {
            "orgName": cfg.get("orgName", ""),
            "productName": cfg.get("productName", ""),
            "description": cfg.get("description", ""),
            "productType": cfg.get("productType", "webapp"),
            "urls": cfg.get("urls") or [],
        },
        "environments": cfg.get("environments") or {},
        "issueTracker": cfg.get("issueTracker") or {},
        "vcs": cfg.get("vcs") or {},
        "publish": cfg.get("publish") or {},
        "knowledge": cfg.get("knowledge") or {},
        "api": cfg.get("api") or {},
        "productQA": {
            "criticalFlows": [], "saveSemantics": "", "publishSemantics": "",
            "keyPages": [], "riskAreas": [], "alwaysCheck": [],
        },
        "anthropicKey": "",
    }
    it = answers["issueTracker"]
    if not it.get("statusMapping"):
        it["statusMapping"] = {"ready_for_qa": ["Ready for QA"], "in_qa": ["In QA"]}
    for _id, path, _key in _secret_specs(cfg):
        if _dig(answers, path) is not None:
            _put(answers, path, "")
    return answers


def secrets_set_map(config: dict, secrets: dict) -> dict:
    secrets = secrets or {}
    out = {field_id: bool(secrets.get(key)) for field_id, _p, key in _secret_specs(config or {})}
    out["anthropicKey"] = bool(secrets.get("ANTHROPIC_API_KEY"))
    return out


def merge_and_build(answers: dict, existing_config: dict, existing_secrets: dict) -> tuple[dict, dict]:
    """Build (config, secrets) from edited answers, keeping blanked secrets and identity."""
    existing_config = existing_config or {}
    existing_secrets = existing_secrets or {}
    new_config, new_secrets = build_instance_config(answers)
    # Preserve onboarded identity — never rename the skill on a productName edit.
    if existing_config.get("appSlug"):
        new_config["appSlug"] = existing_config["appSlug"]
    if existing_config.get("skillCommand"):
        new_config["skillCommand"] = existing_config["skillCommand"]
    # New non-blank secrets override; existing values carried forward.
    final_secrets = {**existing_secrets, **new_secrets}
    # Blank secret field -> restore the ${secret:KEY} ref if a value exists.
    for _id, path, key in _secret_specs(new_config):
        val = _dig(new_config, path)
        if (val is None or val == "") and key in final_secrets:
            _put(new_config, path, _ref(key))
    return new_config, final_secrets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_config_io.py -v`
Expected: 8 passed (3 primitives + 5 config_io).

- [ ] **Step 5: Commit**

```bash
git add backend/config_io.py backend/tests/test_config_io.py
git commit -m "feat(config): config_io — answers mapping + blank-keep merge (#1/#2/#5/#11 core)"
```

---

## Task 3: `GET` + `PUT /api/config` endpoints

**Files:**
- Modify: `backend/server.py`
- Test: `backend/tests/test_config_endpoints.py`

**Interfaces:**
- Consumes: `config_io.*`, `onboarding.write_config_and_secrets`, `onboarding.validate_answers`, `instance_config.read_secrets_file/load_instance_config/load_secrets_env/default_config_dir`.
- Produces (HTTP): `GET /api/config` → `{ok, answers, secretsSet}`; `PUT /api/config` (JSON body = answers) → `{ok}` or `{ok:false, errors}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config_endpoints.py`:
```python
from fastapi.testclient import TestClient
import server


def test_get_config_returns_answers_and_secretsset():
    client = TestClient(server.app)
    res = client.get("/api/config")
    # Live instance is onboarded -> 200 with shape; if not onboarded -> 404 (still valid).
    if res.status_code == 404:
        return
    body = res.json()
    assert body["ok"] is True
    assert "company" in body["answers"]
    assert "issueTracker" in body["answers"]
    assert body["answers"]["issueTracker"]["token"] == ""  # secret blanked
    assert "issueTracker.token" in body["secretsSet"]


def test_put_config_rejects_invalid():
    client = TestClient(server.app)
    res = client.put("/api/config", json={"company": {"productName": ""}})
    assert res.status_code == 400
    assert res.json()["ok"] is False


def test_put_config_roundtrip_blank_keep(tmp_path, monkeypatch):
    # Point config + secrets at a temp dir so the test never mutates the real instance.
    import instance_config, onboarding, importlib
    monkeypatch.setenv("SCRIBE_CONFIG_DIR", str(tmp_path))
    seed_cfg = {
        "orgName": "A", "productName": "P", "productType": "webapp", "description": "",
        "urls": [], "appSlug": "p", "skillCommand": "/qa-evidence-p",
        "environments": {"mode": "static", "staticUrls": ["https://x"]},
        "issueTracker": {"type": "linear", "baseUrl": "", "projects": ["INV"], "email": "",
                         "token": "${secret:LINEAR_TOKEN}", "access": {"read": True, "write": True},
                         "statusMapping": {"ready_for_qa": ["Ready for QA"], "in_qa": ["In QA"]}},
        "vcs": {"type": "github", "org": "", "repos": ["r1"], "token": "${secret:GITHUB_TOKEN}",
                "access": {"read": True, "write": True}},
        "publish": {"jiraComment": True, "prComment": True, "slackWebhook": "",
                    "confluence": {"baseUrl": "", "spaceKey": "", "parentPage": "", "token": ""}},
        "knowledge": {"provider": "none", "link": "", "token": "", "access": {"read": True, "write": False}},
        "api": {},
    }
    onboarding.write_config_and_secrets(seed_cfg, {"LINEAR_TOKEN": "lt", "GITHUB_TOKEN": "gh"}, str(tmp_path))

    client = TestClient(server.app)
    # GET, then edit a non-secret field, leave tokens blank, PUT back.
    answers = client.get("/api/config").json()["answers"]
    answers["vcs"]["repos"] = ["r1", "r2"]
    res = client.put("/api/config", json=answers)
    assert res.status_code == 200 and res.json()["ok"] is True

    import json
    written = json.load(open(tmp_path / "instance.config.json", encoding="utf-8"))
    assert written["vcs"]["repos"] == ["r1", "r2"]            # edit applied
    assert written["issueTracker"]["token"] == "${secret:LINEAR_TOKEN}"  # blank kept ref
    secrets = open(tmp_path / ".secrets.env", encoding="utf-8").read()
    assert "LINEAR_TOKEN=lt" in secrets                        # value kept
    assert "lt" not in json.dumps(written)                     # no real secret in config
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_config_endpoints.py -v`
Expected: FAIL — 404/405 for `/api/config` (routes don't exist).

- [ ] **Step 3: Wire imports + endpoints in `server.py`**

In `backend/server.py`, extend the onboarding import (line 27) to:
```python
from onboarding import validate_answers, run_onboarding, write_config_and_secrets
```
Extend the instance_config import (lines 28-31) to add `read_secrets_file`:
```python
from instance_config import (
    load_instance_config, load_secrets_env, default_config_dir,
    default_skills_root, default_instances_root, read_secrets_file,
)
```
Add `import config_io` near the other local imports (e.g., after `import linear_client`).

Add the endpoints after the existing `POST /api/onboarding` handler (after line 259):
```python
@app.get("/api/config")
async def api_config_get():
    """Current config in the onboarding-form shape, secrets blanked (#1)."""
    cfg = load_instance_config()
    if not cfg:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not onboarded"})
    secrets = read_secrets_file()
    return {
        "ok": True,
        "answers": config_io.config_to_answers(cfg),
        "secretsSet": config_io.secrets_set_map(cfg, secrets),
    }


@app.put("/api/config")
async def api_config_put(answers: Dict[str, Any]):
    """Edit config (#2/#5/#11): merge (blank secret = keep), write, hot-reload secrets."""
    errors = validate_answers(answers)
    if errors:
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors})
    existing = load_instance_config() or {}
    existing_secrets = read_secrets_file()
    new_config, new_secrets = config_io.merge_and_build(answers, existing, existing_secrets)
    paths = write_config_and_secrets(new_config, new_secrets, default_config_dir())
    load_secrets_env(paths["secrets"])  # hot-reload edited tokens — no restart
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_config_endpoints.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/server.py backend/tests/test_config_endpoints.py
git commit -m "feat(api): GET/PUT /api/config — view + edit (#1/#2/#5/#11)"
```

---

## Task 4: Postman upload — `save_postman_collection` + `POST /api/config/upload-postman`

**Files:**
- Modify: `backend/onboarding.py` (add `save_postman_collection`)
- Modify: `backend/server.py` (endpoint + `UploadFile, File` import)
- Modify: `backend/requirements.txt` (+`python-multipart`)
- Test: `backend/tests/test_postman_upload.py`

**Interfaces:**
- Produces: `onboarding.save_postman_collection(content: bytes, config: dict, config_dir: str) -> tuple[dict, int]` (raises `ValueError` on non-JSON); `POST /api/config/upload-postman` (multipart `file`) → `{ok, endpointCount, path}`.

- [ ] **Step 1: Add `python-multipart` and install it**

Add to `backend/requirements.txt`:
```
python-multipart==0.0.9
```
Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pip install python-multipart==0.0.9`
Expected: "Successfully installed python-multipart-0.0.9" (or already satisfied).

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_postman_upload.py`:
```python
import json
import onboarding


def _collection():
    return json.dumps({
        "info": {"name": "API"},
        "item": [
            {"name": "Users", "item": [
                {"name": "list", "request": {"method": "GET", "url": {"raw": "{{BASE}}/users"}}},
                {"name": "create", "request": {"method": "POST", "url": {"raw": "{{BASE}}/users"}}},
            ]},
        ],
    }).encode("utf-8")


def test_save_postman_sets_path_and_counts(tmp_path):
    cfg = {"appSlug": "beeventory", "api": {"baseUrl": "https://api"}}
    cfg2, count = onboarding.save_postman_collection(_collection(), cfg, str(tmp_path))
    assert count == 2
    assert cfg2["api"]["postmanCollectionPath"].endswith("beeventory.postman_collection.json")
    assert (tmp_path / "beeventory.postman_collection.json").exists()


def test_save_postman_rejects_non_json(tmp_path):
    cfg = {"appSlug": "x", "api": {}}
    import pytest
    with pytest.raises(ValueError):
        onboarding.save_postman_collection(b"not json {", cfg, str(tmp_path))


def test_upload_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server, onboarding
    monkeypatch.setenv("SCRIBE_CONFIG_DIR", str(tmp_path))
    onboarding.write_config_and_secrets(
        {"appSlug": "beeventory", "productName": "B", "api": {}}, {}, str(tmp_path))
    client = TestClient(server.app)
    res = client.post("/api/config/upload-postman",
                      files={"file": ("c.json", _collection(), "application/json")})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["endpointCount"] == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_postman_upload.py -v`
Expected: FAIL — `AttributeError: ... 'save_postman_collection'` / 404 on upload route.

- [ ] **Step 4: Add `save_postman_collection` to `onboarding.py`**

In `backend/onboarding.py`, add near `_parse_postman_endpoints` (after line 216):
```python
def save_postman_collection(content: bytes, config: dict, config_dir: str) -> tuple[dict, int]:
    """Validate the bytes are a JSON Postman collection, store under config_dir as
    {appSlug}.postman_collection.json, set config.api.postmanCollectionPath, and return
    (config, endpoint_count). Raises ValueError on invalid JSON. Does NOT rewrite the skill."""
    try:
        json.loads(content.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"not a valid JSON Postman collection: {e}")
    slug = config.get("appSlug") or "app"
    os.makedirs(config_dir, exist_ok=True)
    dest = os.path.join(config_dir, f"{slug}.postman_collection.json")
    with open(dest, "wb") as fh:
        fh.write(content)
    config.setdefault("api", {})
    config["api"]["postmanCollectionPath"] = dest
    groups = _parse_postman_endpoints(dest)
    return config, sum(len(v) for v in groups.values())
```

- [ ] **Step 5: Add the upload endpoint to `server.py`**

Extend the FastAPI import (line 11):
```python
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
```
Extend the onboarding import (line 27) to add `save_postman_collection`:
```python
from onboarding import validate_answers, run_onboarding, write_config_and_secrets, save_postman_collection
```
Add after `PUT /api/config`:
```python
@app.post("/api/config/upload-postman")
async def api_upload_postman(file: UploadFile = File(...)):
    """Store an uploaded Postman collection, set its path, re-parse for a count (#3)."""
    cfg = load_instance_config()
    if not cfg:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not onboarded"})
    content = await file.read()
    try:
        cfg, count = save_postman_collection(content, cfg, default_config_dir())
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    write_config_and_secrets(cfg, read_secrets_file(), default_config_dir())
    return {"ok": True, "endpointCount": count, "path": cfg["api"]["postmanCollectionPath"]}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/test_postman_upload.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/onboarding.py backend/server.py backend/requirements.txt backend/tests/test_postman_upload.py
git commit -m "feat(#3): Postman upload — store + path + re-parse count"
```

---

## Task 5: Frontend — extract shared field components

**Files:**
- Create: `frontend/src/components/Onboarding/fields.tsx`
- Modify: `frontend/src/components/Onboarding/OnboardingWizard.tsx` (import from the new module; remove the local copies)

**Interfaces:**
- Produces: `AccessChecks`, `ListTextarea`, `Field`, `linesToArr`, `arrToLines` exported from `fields.tsx`.

- [ ] **Step 1: Create `fields.tsx` with the extracted components**

Create `frontend/src/components/Onboarding/fields.tsx`:
```tsx
import { useState, type ReactNode } from 'react'
import { Access } from '../../onboardingSchema'

export const linesToArr = (s: string) => s.split('\n').map((l) => l.trim()).filter(Boolean)
export const arrToLines = (a: string[]) => a.join('\n')

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="ob-field">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function AccessChecks({ value, onChange }: { value: Access; onChange: (a: Access) => void }) {
  return (
    <div className="ob-access">
      <span className="ob-access-label">Access:</span>
      <label>
        <input type="checkbox" checked={value.read} onChange={(e) => onChange({ ...value, read: e.target.checked })} /> read
      </label>
      <label>
        <input type="checkbox" checked={value.write} onChange={(e) => onChange({ ...value, write: e.target.checked })} /> write
      </label>
    </div>
  )
}

export function ListTextarea({
  value, onChange, rows = 3, placeholder,
}: {
  value: string[]
  onChange: (v: string[]) => void
  rows?: number
  placeholder?: string
}) {
  const [text, setText] = useState(() => arrToLines(value))
  return (
    <textarea
      rows={rows}
      placeholder={placeholder}
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        onChange(linesToArr(e.target.value))
      }}
    />
  )
}
```

- [ ] **Step 2: Update `OnboardingWizard.tsx` to import them**

In `frontend/src/components/Onboarding/OnboardingWizard.tsx`:
1. Add to the imports (after the `onboardingSchema` import, line ~10): `import { AccessChecks, ListTextarea, Field, linesToArr, arrToLines } from './fields'`
2. Delete the now-duplicate local definitions: `linesToArr`/`arrToLines` (lines 26-27), `AccessChecks` (38-50), `Field` (52-59), and `ListTextarea` (65-88). Leave all other helpers (e.g. `STATUS_DEFAULTS`) intact.

- [ ] **Step 3: Verify typecheck**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds (wizard now imports the shared components; no duplicate-identifier or unused errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Onboarding/fields.tsx frontend/src/components/Onboarding/OnboardingWizard.tsx
git commit -m "refactor(onboarding): extract AccessChecks/ListTextarea/Field to fields.tsx"
```

---

## Task 6: Frontend API client — `getConfig` / `updateConfig` / `uploadPostman`

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: `getConfig(): Promise<ConfigResponse>`, `updateConfig(answers): Promise<{ok:boolean; errors?:string[]}>`, `uploadPostman(file: File): Promise<{ok:boolean; endpointCount?:number; error?:string}>`, and `ConfigResponse = { ok: boolean; answers: OnboardingAnswers; secretsSet: Record<string, boolean> }`.

- [ ] **Step 1: Add the functions**

In `frontend/src/api.ts`, add (import `OnboardingAnswers` from `./onboardingSchema` at the top if not already imported):
```ts
import type { OnboardingAnswers } from './onboardingSchema'

export interface ConfigResponse {
  ok: boolean
  answers: OnboardingAnswers
  secretsSet: Record<string, boolean>
}

export async function getConfig(): Promise<ConfigResponse> {
  const res = await fetch(`${BASE}/config`)
  if (!res.ok) throw new Error(`getConfig failed: ${res.status}`)
  return res.json()
}

export async function updateConfig(answers: OnboardingAnswers): Promise<{ ok: boolean; errors?: string[] }> {
  const res = await fetch(`${BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(answers),
  })
  return res.json().catch(() => ({ ok: false, errors: [`status ${res.status}`] }))
}

export async function uploadPostman(file: File): Promise<{ ok: boolean; endpointCount?: number; error?: string }> {
  const form = new FormData()
  form.append('file', file)
  // NOTE: do NOT set Content-Type — the browser sets the multipart boundary.
  const res = await fetch(`${BASE}/config/upload-postman`, { method: 'POST', body: form })
  return res.json().catch(() => ({ ok: false, error: `status ${res.status}` }))
}
```

- [ ] **Step 2: Verify typecheck**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(api-client): getConfig / updateConfig / uploadPostman (FormData)"
```

---

## Task 7: Frontend — `Settings.tsx` + header ⚙ + App wiring

**Files:**
- Create: `frontend/src/components/Settings.tsx`
- Modify: `frontend/src/components/TopBar.tsx` (⚙ button + prop), `frontend/src/App.tsx` (modal state + render)

**Interfaces:**
- Consumes: `getConfig`, `updateConfig`, `uploadPostman` (Task 6), `Modal`, `AccessChecks`/`ListTextarea`/`Field` (Task 5), `OnboardingAnswers`.

- [ ] **Step 1: Create `Settings.tsx`**

Create `frontend/src/components/Settings.tsx`:
```tsx
import { useEffect, useState } from 'react'
import Modal from './Modal'
import { Field, AccessChecks, ListTextarea } from './Onboarding/fields'
import type { OnboardingAnswers } from '../onboardingSchema'
import { getConfig, updateConfig, uploadPostman } from '../api'

// Masked secret input: blank submit keeps the existing secret; typing replaces it.
function SecretInput({ isSet, value, onChange }: { isSet: boolean; value: string; onChange: (v: string) => void }) {
  return (
    <input
      type="password"
      value={value}
      placeholder={isSet ? '•••• set — leave blank to keep' : 'not set'}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export default function Settings({ onClose }: { onClose: () => void }) {
  const [a, setA] = useState<OnboardingAnswers | null>(null)
  const [secretsSet, setSecretsSet] = useState<Record<string, boolean>>({})
  const [status, setStatus] = useState<string>('')
  const [postmanMsg, setPostmanMsg] = useState<string>('')

  useEffect(() => {
    getConfig().then((r) => { setA(r.answers); setSecretsSet(r.secretsSet) }).catch((e) => setStatus(String(e)))
  }, [])

  if (!a) {
    return <Modal title="Settings — Config Center" onClose={onClose}><p>Loading…</p></Modal>
  }

  // section-merge helper mirroring the wizard's set()
  function set<K extends keyof OnboardingAnswers>(section: K, patch: Partial<OnboardingAnswers[K]>) {
    setA((prev) => prev ? { ...prev, [section]: { ...(prev[section] as object), ...patch } as OnboardingAnswers[K] } : prev)
  }

  async function save() {
    setStatus('Saving…')
    const res = await updateConfig(a!)
    setStatus(res.ok ? 'Saved ✓' : `Error: ${(res.errors || []).join('; ')}`)
  }

  async function onPostman(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    setPostmanMsg('Uploading…')
    const r = await uploadPostman(f)
    setPostmanMsg(r.ok ? `Stored — ${r.endpointCount} endpoints parsed` : `Error: ${r.error}`)
  }

  const it = a.issueTracker, vcs = a.vcs, env = a.environments, pub = a.publish, kn = a.knowledge, api = a.api

  return (
    <Modal
      title="Settings — Config Center"
      onClose={onClose}
      actions={<>
        <span style={{ marginRight: 'auto', fontSize: 12, color: 'var(--text-dim)' }}>{status}</span>
        <button className="btn btn--primary" onClick={save}>Save</button>
      </>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '70vh', overflowY: 'auto' }}>
        <section>
          <h4>Company &amp; product</h4>
          <Field label="Org name"><input value={a.company.orgName} onChange={(e) => set('company', { orgName: e.target.value })} /></Field>
          <Field label="Product name"><input value={a.company.productName} onChange={(e) => set('company', { productName: e.target.value })} /></Field>
          <Field label="Description"><input value={a.company.description} onChange={(e) => set('company', { description: e.target.value })} /></Field>
          <Field label="URLs (one per line)"><ListTextarea rows={2} value={a.company.urls} onChange={(urls) => set('company', { urls })} /></Field>
        </section>

        <section>
          <h4>Issue tracker</h4>
          <Field label="Base URL"><input value={it.baseUrl} onChange={(e) => set('issueTracker', { baseUrl: e.target.value })} /></Field>
          <Field label="Project keys (one per line)"><ListTextarea rows={2} value={it.projects} onChange={(projects) => set('issueTracker', { projects })} /></Field>
          <Field label="Account email"><input value={it.email} onChange={(e) => set('issueTracker', { email: e.target.value })} /></Field>
          <Field label="API token"><SecretInput isSet={!!secretsSet['issueTracker.token']} value={it.token} onChange={(token) => set('issueTracker', { token })} /></Field>
          <AccessChecks value={it.access} onChange={(access) => set('issueTracker', { access })} />
        </section>

        <section>
          <h4>Version control</h4>
          <Field label="Org / workspace"><input value={vcs.org} onChange={(e) => set('vcs', { org: e.target.value })} /></Field>
          <Field label="Repos (one per line)"><ListTextarea rows={3} value={vcs.repos} onChange={(repos) => set('vcs', { repos })} /></Field>
          <Field label="API token"><SecretInput isSet={!!secretsSet['vcs.token']} value={vcs.token} onChange={(token) => set('vcs', { token })} /></Field>
          <AccessChecks value={vcs.access} onChange={(access) => set('vcs', { access })} />
        </section>

        <section>
          <h4>Test login</h4>
          <Field label="Login URL"><input value={env.testAuth?.loginUrl || ''} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, loginUrl: e.target.value } })} /></Field>
          <Field label="Username"><input value={env.testAuth?.username || ''} onChange={(e) => set('environments', { testAuth: { ...env.testAuth, username: e.target.value } })} /></Field>
          <Field label="Password"><SecretInput isSet={!!secretsSet['environments.testAuth.password']} value={env.testAuth?.password || ''} onChange={(v) => set('environments', { testAuth: { ...env.testAuth, password: v } })} /></Field>
        </section>

        <section>
          <h4>Knowledge</h4>
          <Field label="Link"><input value={kn.link} onChange={(e) => set('knowledge', { link: e.target.value })} /></Field>
          <Field label="Token"><SecretInput isSet={!!secretsSet['knowledge.token']} value={kn.token} onChange={(token) => set('knowledge', { token })} /></Field>
          <AccessChecks value={kn.access} onChange={(access) => set('knowledge', { access })} />
        </section>

        <section>
          <h4>API / Postman</h4>
          <Field label="Base URL"><input value={api.baseUrl || ''} onChange={(e) => set('api', { ...api, baseUrl: e.target.value })} /></Field>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>Collection: {api.postmanCollectionPath || '(none)'}</div>
          <input type="file" accept="application/json,.json" onChange={onPostman} />
          {postmanMsg && <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>{postmanMsg}</div>}
        </section>
      </div>
    </Modal>
  )
}
```

- [ ] **Step 2: Add a ⚙ button to `TopBar.tsx`**

In `frontend/src/components/TopBar.tsx`: add `onOpenSettings: () => void` to the `Props` interface, destructure it, and add a button in `top-bar__actions` (next to the theme toggle):
```tsx
        <button className="btn btn--ghost btn--small" onClick={onOpenSettings} title="Settings — Config Center">⚙</button>
```

- [ ] **Step 3: Wire the modal in `App.tsx`**

In `frontend/src/App.tsx`:
1. Add import: `import Settings from './components/Settings'`
2. Add state: `const [showSettings, setShowSettings] = useState(false)`
3. Pass to `<TopBar ... onOpenSettings={() => setShowSettings(true)} />`
4. Render near the other modals (e.g., where `CleanupEnvModal`/`HuddleModal` are conditionally rendered):
```tsx
      {showSettings && <Settings onClose={() => setShowSettings(false)} />}
```

- [ ] **Step 4: Verify typecheck + manual**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: build succeeds. Manual (after a backend restart loads the new endpoints): click ⚙ → Settings opens pre-filled; tokens show "•••• set — leave blank to keep"; edit `vcs.repos`, toggle an access box, **Save** → "Saved ✓"; pick a Postman `.json` → "Stored — N endpoints parsed". **#1/#2/#3/#5/#11 all demoed.**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Settings.tsx frontend/src/components/TopBar.tsx frontend/src/App.tsx
git commit -m "feat(cluster-a): Settings (Config Center) modal + header gear (#1/#2/#3/#5/#11 UI)"
```

---

## Task 8: Full sweep + integration smoke

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `cd C:\Users\ankit\SCRIBE\backend && ..\.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_github_client.py`
Expected: all new Cluster A tests pass; only the documented pre-existing WinError failures (test_chat/council/quartermaster) remain — confirm no NEW failures.

- [ ] **Step 2: Frontend build**

Run: `cd C:\Users\ankit\SCRIBE\frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Live smoke (after restarting the backend)**

Restart the backend so it loads `config_io` + the new routes. `GET /api/config` returns the masked answers; open ⚙ in the UI; edit a non-secret field + Save; re-open to confirm persistence; upload a small Postman collection and confirm the endpoint count. Confirm `instance.config.json` still contains only `${secret:}` refs (no real tokens) after a save.

- [ ] **Step 4: Final commit (if any cleanup)**

```bash
git add -A && git commit -m "test(cluster-a): full sweep + integration smoke notes"
```

---

## Self-review (author)

- **Spec coverage:** #1 → Tasks 2,3 (config_to_answers + GET). #2 → Tasks 1,2,3 (merge_and_build + PUT). #3 → Task 4 (save_postman_collection + upload). #5 → repos round-trip through Tasks 2,3,7 (ListTextarea ↔ vcs.repos). #11 → access toggles round-trip through Tasks 2,3,7 (AccessChecks ↔ access). UI for all → Tasks 5,6,7.
- **Invariants:** secrets never written into `instance.config.json` (asserted in tests `test_write_config_and_secrets_writes_both`, `test_merge_blank_token_keeps_existing_ref_and_value`, `test_put_config_roundtrip_blank_keep`); identity preserved (`test_merge_preserves_identity_on_productname_edit`); validate_answers reused (token-agnostic); SKILL.md not rewritten on upload (Task 4 has no render_skill call).
- **Type consistency:** `config_io.config_to_answers`/`secrets_set_map`/`merge_and_build` signatures match across Tasks 2/3; `ConfigResponse {ok, answers, secretsSet}` (Task 6) matches `GET /api/config` (Task 3); `secretsSet` field-ids (`issueTracker.token`, `vcs.token`, `environments.testAuth.password`, `knowledge.token`, `anthropicKey`) used in `Settings.tsx` (Task 7) match `_secret_specs` ids (Task 2).
- **Open risk carried:** changing an integration type with a blank token leaves the token empty (documented in Task 2 behavior + spec).

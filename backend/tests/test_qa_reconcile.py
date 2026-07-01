"""Tests for qa_reconcile — current-main-authoritative reconciliation + divergence guard.

The core reconcile() is pure: gh access is injected as fetch_pr_files / fetch_blob so
the divergence logic is tested offline (spec §7,
docs/superpowers/specs/2026-06-29-main-reconciliation-design.md).
"""
import qa_reconcile
import qa_scoring


def _files(mapping):
    """mapping: {(repo, pr_id): [file dicts]} -> a fetch_pr_files callable."""
    return lambda repo, pr_id: mapping.get((repo, pr_id), [])


def _blobs(mapping):
    """mapping: {(repo, ref, path): content} -> a fetch_blob callable."""
    return lambda repo, ref, path: mapping.get((repo, ref, path))


def test_no_divergence_when_pr_head_and_main_agree():
    prs = [{"repo": "xinventory-ux", "id": 238, "branch": "INV-624-mrdt"}]
    files = _files({("xinventory-ux", 238): [
        {"path": "fees.py", "status": "modified",
         "patch": "@@ -1,1 +1,1 @@\n-MRDT_RATE = 0.05\n+MRDT_RATE = 0.03\n"},
    ]})
    blobs = _blobs({
        ("xinventory-ux", "INV-624-mrdt", "fees.py"): "MRDT_RATE = 0.03\n",
        ("xinventory-ux", "main", "fees.py"): "MRDT_RATE = 0.03\n",
    })
    res = qa_reconcile.reconcile("INV-624", prs, fetch_pr_files=files, fetch_blob=blobs)

    assert res["status"] == "ok"
    assert res["divergences"] == []
    assert {"repo": "xinventory-ux", "path": "fees.py"} in res["touched_files"]
    assert res["main_snapshot"]["xinventory-ux:fees.py"] == "MRDT_RATE = 0.03\n"
    assert res["pr_snapshot"]["xinventory-ux:fees.py"] == "MRDT_RATE = 0.03\n"


def test_divergence_when_main_superseded_pr_value():
    # PR set MRDT 3%; a later main commit changed it to 2%. Re-verifying the PR must
    # NOT pass on 3% — main (2%) is authoritative.
    prs = [{"repo": "xinventory-ux", "id": 238, "branch": "INV-624-mrdt"}]
    files = _files({("xinventory-ux", 238): [
        {"path": "fees.py", "status": "modified",
         "patch": "@@ -1,1 +1,1 @@\n-MRDT_RATE = 0.05\n+MRDT_RATE = 0.03\n"},
    ]})
    blobs = _blobs({
        ("xinventory-ux", "INV-624-mrdt", "fees.py"): "MRDT_RATE = 0.03\n",
        ("xinventory-ux", "main", "fees.py"): "MRDT_RATE = 0.02\n",
    })
    res = qa_reconcile.reconcile("INV-624", prs, fetch_pr_files=files, fetch_blob=blobs)

    assert res["status"] == "ok"
    assert len(res["divergences"]) == 1
    d = res["divergences"][0]
    assert d["repo"] == "xinventory-ux"
    assert d["path"] == "fees.py"
    assert d["pr_hint"] == "MRDT_RATE = 0.03"
    assert d["main_hint"] == "MRDT_RATE = 0.02"


def test_reconcile_skips_test_and_spec_files():
    # A later main commit changed BOTH a test fixture and a product constant. Only the
    # product file is an AC value — test-fixture churn must NOT produce a divergence
    # (it would otherwise wrongly block a pass on mock data). Real case: INV-561.
    prs = [{"repo": "xinventory-services", "id": 159, "branch": "INV-561",
            "head_sha": "SHA"}]
    files = _files({("xinventory-services", 159): [
        {"path": "src/functions/payment-service/src/__tests__/sales-order.service.test.ts",
         "patch": "@@\n+name: 'ROOM_CHARGES',\n"},
        {"path": "src/functions/payment-service/src/services/fees.ts",
         "patch": "@@\n+MRDT = 0.03\n"},
    ]})
    blobs = _blobs({
        ("xinventory-services", "SHA",
         "src/functions/payment-service/src/__tests__/sales-order.service.test.ts"):
            "name: 'ROOM_CHARGES',\n",
        ("xinventory-services", "main",
         "src/functions/payment-service/src/__tests__/sales-order.service.test.ts"):
            "name: 'Suite A Room',\n",
        ("xinventory-services", "SHA",
         "src/functions/payment-service/src/services/fees.ts"): "MRDT = 0.03\n",
        ("xinventory-services", "main",
         "src/functions/payment-service/src/services/fees.ts"): "MRDT = 0.02\n",
    })
    res = qa_reconcile.reconcile("INV-561", prs, fetch_pr_files=files, fetch_blob=blobs)

    paths = [d["path"] for d in res["divergences"]]
    assert paths == ["src/functions/payment-service/src/services/fees.ts"]
    assert all("__tests__" not in p and ".test." not in p for p in paths)


def test_is_reconcilable_path_excludes_nonproduct_files():
    ok = qa_reconcile._is_reconcilable_path
    assert ok("src/services/fees.ts")
    assert ok("src/components/folio/FolioRenderer.tsx")
    assert not ok("src/x/__tests__/a.test.ts")
    assert not ok("src/x/a.spec.ts")
    assert not ok("src/x/__mocks__/a.ts")
    assert not ok("src/x/__snapshots__/a.snap")
    assert not ok("src/x/Button.stories.tsx")


def test_multi_repo_both_processed():
    prs = [
        {"repo": "xinventory-services", "id": 182, "branch": "INV-624-be"},
        {"repo": "xinventory-ux", "id": 238, "branch": "INV-624-fe"},
    ]
    files = _files({
        ("xinventory-services", 182): [
            {"path": "svc.py", "patch": "@@\n+GST = 0.05\n"}],
        ("xinventory-ux", 238): [
            {"path": "ui.tsx", "patch": "@@\n+const mrdt = 0.03\n"}],
    })
    blobs = _blobs({
        ("xinventory-services", "INV-624-be", "svc.py"): "GST = 0.05\n",
        ("xinventory-services", "main", "svc.py"): "GST = 0.05\n",
        ("xinventory-ux", "INV-624-fe", "ui.tsx"): "const mrdt = 0.03\n",
        ("xinventory-ux", "main", "ui.tsx"): "const mrdt = 0.02\n",
    })
    res = qa_reconcile.reconcile("INV-624", prs, fetch_pr_files=files, fetch_blob=blobs)

    assert res["status"] == "ok"
    assert {"repo": "xinventory-services", "path": "svc.py"} in res["touched_files"]
    assert {"repo": "xinventory-ux", "path": "ui.tsx"} in res["touched_files"]
    # svc.py agrees with main; ui.tsx's mrdt was superseded -> exactly one divergence
    assert [d["path"] for d in res["divergences"]] == ["ui.tsx"]


def test_degraded_when_gh_fetch_fails():
    prs = [{"repo": "xinventory-ux", "id": 238, "branch": "INV-624"}]

    def boom(repo, pr_id):
        raise RuntimeError("gh: HTTP 401 Unauthorized")

    res = qa_reconcile.reconcile(
        "INV-624", prs, fetch_pr_files=boom, fetch_blob=_blobs({}))

    assert res["status"] == "degraded"
    assert res["degraded_reason"]
    assert "401" in res["degraded_reason"]
    assert res["divergences"] == []


def test_build_tcs_degraded_emits_scoring_tc_recon():
    res = {"status": "degraded", "degraded_reason": "gh down", "divergences": []}
    tcs = qa_reconcile.build_reconcile_tcs(res)

    assert len(tcs) == 1
    assert tcs[0]["id"] == "TC-RECON"
    assert tcs[0]["status"] == "needs-review"
    assert "unavailable" in tcs[0]["note"].lower()
    # AC-tied: the scoring policy must COUNT it (so a degraded run can't silently pass)
    assert qa_scoring.classify_tc(tcs[0]["id"]) == "scoring"


def test_build_tcs_maps_divergence_to_scoring_needs_review():
    res = {
        "status": "ok", "degraded_reason": None,
        "divergences": [
            {"repo": "xinventory-ux", "path": "fees.py", "region": "MRDT_RATE",
             "pr_hint": "MRDT_RATE = 0.03", "main_hint": "MRDT_RATE = 0.02"},
            # No main_hint -> advisory divergence, NOT a scoring TC.
            {"repo": "xinventory-ux", "path": "x.py", "region": "foo",
             "pr_hint": "foo = 1", "main_hint": None},
        ],
    }
    tcs = qa_reconcile.build_reconcile_tcs(res)

    assert len(tcs) == 1
    tc = tcs[0]
    assert tc["status"] == "needs-review"
    assert "MRDT_RATE = 0.03" in tc["note"] and "MRDT_RATE = 0.02" in tc["note"]
    assert qa_scoring.classify_tc(tc["id"]) == "scoring"


def test_build_tcs_no_divergence_no_tcs():
    res = {"status": "ok", "degraded_reason": None, "divergences": []}
    assert qa_reconcile.build_reconcile_tcs(res) == []


def test_build_tcs_groups_divergences_per_file():
    # Many changed lines in one file -> ONE needs-review TC (summarized), not N. Keeps
    # a genuinely-superseded file from spawning dozens of score-tanking TCs (INV-588).
    res = {"status": "ok", "degraded_reason": None, "divergences": [
        {"repo": "r", "path": "a.ts", "region": "X", "pr_hint": "X = 1", "main_hint": "X = 2"},
        {"repo": "r", "path": "a.ts", "region": "Y", "pr_hint": "Y = 3", "main_hint": "Y = 4"},
        {"repo": "r", "path": "b.ts", "region": "Z", "pr_hint": "Z = 5", "main_hint": "Z = 6"},
    ]}
    tcs = qa_reconcile.build_reconcile_tcs(res)
    assert len(tcs) == 2                                   # one per file
    assert sorted(t["path"] for t in tcs) == ["a.ts", "b.ts"]
    a = next(t for t in tcs if t["path"] == "a.ts")
    assert "+1 more" in a["note"]                          # the file's extra divergence is noted
    assert all(qa_scoring.classify_tc(t["id"]) == "scoring" for t in tcs)


def test_reconcile_live_resolves_head_sha_then_delegates():
    # PR ref lacks head_sha; reconcile_live resolves it via fetch_pr so blob fetches
    # use the immutable sha (a merged PR's branch may be deleted).
    prs = [{"repo": "xinventory-ux", "id": 238, "branch": "orig"}]

    def fetch_pr(repo, pr_id):
        return {"repo": repo, "id": pr_id, "branch": "fix/x", "head_sha": "SHA61d7"}

    files = _files({("xinventory-ux", 238): [
        {"path": "fees.py", "patch": "@@\n+MRDT = 0.03\n"}]})
    blobs = _blobs({
        ("xinventory-ux", "SHA61d7", "fees.py"): "MRDT = 0.03\n",   # fetched by sha
        ("xinventory-ux", "main", "fees.py"): "MRDT = 0.02\n",
    })
    res = qa_reconcile.reconcile_live(
        "INV-624", prs, fetch_pr=fetch_pr, fetch_pr_files=files, fetch_blob=blobs)

    assert res["status"] == "ok"
    assert len(res["divergences"]) == 1
    assert res["divergences"][0]["main_hint"] == "MRDT = 0.02"


def test_reconcile_live_degrades_when_ref_resolution_fails():
    prs = [{"repo": "xinventory-ux", "id": 238}]

    def fetch_pr(repo, pr_id):
        raise RuntimeError("gh: 404 Not Found")

    res = qa_reconcile.reconcile_live(
        "INV-624", prs, fetch_pr=fetch_pr, fetch_pr_files=_files({}), fetch_blob=_blobs({}))

    assert res["status"] == "degraded"
    assert "404" in res["degraded_reason"]
    # the guard must still fire so the run can't silently pass
    assert qa_reconcile.build_reconcile_tcs(res)[0]["id"] == "TC-RECON"

"""Regression tests for the three INV-675 dashboard bugs:

1. Duplicate runs → the dashboard must display the BEST completed run, not the
   lexicographically-latest (`_select_display_run`).
2. No screenshots → a thin (image-less) report whose run has PNGs on disk must be
   regenerated with embedded screenshots (`_report_missing_screenshots` + the
   regen hook in `check_evidence`).
3. Stale report until refresh → the report URL must carry a cache-bust version so
   a regenerated report is fetched fresh (`_report_url_for` `?v=<mtime>`).
"""
import json
import os

import agents


def _runs_path(tmp_path, key="INV-675"):
    p = os.path.join(str(tmp_path), key, "runs")
    os.makedirs(p, exist_ok=True)
    return p


def _make_run(runs_path, name, *, confidence=None, index_bytes=None, pngs=()):
    run_dir = os.path.join(runs_path, name)
    os.makedirs(os.path.join(run_dir, "automated"), exist_ok=True)
    if confidence is not None:
        summary = {"ticket": "INV-675", "confidence": {"headline": confidence}}
        with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f)
    if index_bytes is not None:
        with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("x" * index_bytes)
    for rel in pngs:
        dst = os.path.join(run_dir, "automated", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(b"\x89PNG\r\n")
    return run_dir


class TestSelectDisplayRun:
    def test_prefers_highest_confidence_completed_run(self, tmp_path):
        rp = _runs_path(tmp_path)
        _make_run(rp, "run-qa-feature-dev-001", confidence=92, index_bytes=100)
        _make_run(rp, "run-qa-feature-dev-002", confidence=88, index_bytes=100)
        assert agents._select_display_run(rp) == "run-qa-feature-dev-001"

    def test_tie_breaks_on_newest_run(self, tmp_path):
        rp = _runs_path(tmp_path)
        _make_run(rp, "run-qa-feature-dev-001", confidence=90, index_bytes=100)
        _make_run(rp, "run-qa-feature-dev-002", confidence=90, index_bytes=100)
        assert agents._select_display_run(rp) == "run-qa-feature-dev-002"

    def test_falls_back_to_newest_when_none_completed(self, tmp_path):
        rp = _runs_path(tmp_path)
        _make_run(rp, "run-qa-feature-dev-001")
        _make_run(rp, "run-qa-feature-dev-002")
        assert agents._select_display_run(rp) == "run-qa-feature-dev-002"

    def test_completed_preferred_over_newer_in_progress(self, tmp_path):
        rp = _runs_path(tmp_path)
        _make_run(rp, "run-qa-feature-dev-001", confidence=80, index_bytes=100)
        _make_run(rp, "run-qa-feature-dev-002")  # in-progress, no summary
        assert agents._select_display_run(rp) == "run-qa-feature-dev-001"

    def test_none_when_no_runs(self, tmp_path):
        assert agents._select_display_run(_runs_path(tmp_path)) is None


class TestReportUrlCacheBust:
    def test_appends_version_when_index_exists(self, tmp_path):
        rp = _runs_path(tmp_path)
        run_dir = _make_run(rp, "run-1", confidence=90, index_bytes=100)
        url = agents._report_url_for("INV-675", "run-1", run_dir)
        assert url.startswith("/evidence/INV-675/runs/run-1/index.html?v=")
        v = int(url.split("?v=")[1])
        assert v == int(os.path.getmtime(os.path.join(run_dir, "index.html")))

    def test_empty_when_no_index(self, tmp_path):
        rp = _runs_path(tmp_path)
        run_dir = _make_run(rp, "run-1", confidence=90)  # no index.html
        assert agents._report_url_for("INV-675", "run-1", run_dir) == ""

    def test_empty_when_no_runname(self):
        assert agents._report_url_for("INV-675", "", None) == ""


class TestMissingScreenshots:
    def test_thin_report_with_pngs_needs_regen(self, tmp_path):
        rp = _runs_path(tmp_path)
        run_dir = _make_run(rp, "run-1", confidence=90, index_bytes=5000,
                            pngs=["TC-1/live.png"])
        assert agents._report_missing_screenshots(run_dir) is True

    def test_large_embedded_report_is_ok(self, tmp_path):
        rp = _runs_path(tmp_path)
        run_dir = _make_run(rp, "run-1", confidence=90, index_bytes=200_000,
                            pngs=["TC-1/live.png"])
        assert agents._report_missing_screenshots(run_dir) is False

    def test_no_pngs_means_no_regen(self, tmp_path):
        rp = _runs_path(tmp_path)
        run_dir = _make_run(rp, "run-1", confidence=90, index_bytes=5000)
        assert agents._report_missing_screenshots(run_dir) is False

    def test_missing_index_is_not_flagged_here(self, tmp_path):
        # missing index is handled by the 'report missing' branch, not this one
        rp = _runs_path(tmp_path)
        run_dir = _make_run(rp, "run-1", confidence=90, pngs=["TC-1/live.png"])
        assert agents._report_missing_screenshots(run_dir) is False


class TestCheckEvidenceSelection:
    def test_check_evidence_serves_best_run_with_cachebust(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agents, "EVIDENCE_DIR", str(tmp_path))
        rp = _runs_path(tmp_path)
        _make_run(rp, "run-qa-feature-dev-001", confidence=92, index_bytes=100)
        _make_run(rp, "run-qa-feature-dev-002", confidence=88, index_bytes=100)
        result = agents.check_evidence("INV-675")
        assert result["latestRun"] == "run-qa-feature-dev-001"
        assert "run-qa-feature-dev-001" in result["reportUrl"]
        assert "?v=" in result["reportUrl"]
        assert result["score"] == 92

    def test_check_evidence_regenerates_stuck_imageless_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agents, "EVIDENCE_DIR", str(tmp_path))
        rp = _runs_path(tmp_path)
        _make_run(rp, "run-1", confidence=92, index_bytes=5000, pngs=["TC-1/live.png"])
        calls = []

        def _spy(key, run=None):
            calls.append((key, run))
            return True, "ok", ""
        monkeypatch.setattr(agents, "generate_html_report", _spy)
        agents.check_evidence("INV-675")
        assert calls == [("INV-675", "run-1")]


def test_check_new_evidence_coerces_dict_score_to_int(tmp_path, monkeypatch):
    """check_new_evidence must return score as int, never as a dict."""
    monkeypatch.setattr(agents, "EVIDENCE_DIR", str(tmp_path))
    run_dir = os.path.join(str(tmp_path), "INV-XXX", "runs", "run-1")
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"score": {"pass": 2, "fail": 0, "total": 2, "pct": 88}}, f)
    with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write("x" * 100)

    spy_calls = []

    def _spy(key, run=None):
        spy_calls.append((key, run))
        return True, "ok", ""
    monkeypatch.setattr(agents, "generate_html_report", _spy)

    result = agents.check_new_evidence("INV-XXX", [])
    assert result["found"] is True
    assert result["score"] == 88
    assert isinstance(result["score"], int)


def test_report_splits_advisory_and_uses_canonical_headline(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setattr(agents, "EVIDENCE_DIR", str(tmp_path))
    run_dir = os.path.join(str(tmp_path), "INV-701", "runs", "run-1")
    os.makedirs(run_dir)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        _json.dump({
            "ticket": "INV-701", "verdict": "PASS",
            "score": {"pass": 1, "fail": 0, "total": 1, "pct": 100},
            "test_cases": [
                {"id": "TC-701-001", "title": "AC one", "status": "pass"},
                {"id": "TC-API-user-1", "title": "GET user", "status": "fail"},
                {"id": "TC-UV-5", "title": "a11y", "status": "fail"},
            ],
        }, f)
    ok, msg, url = agents.generate_html_report("INV-701", "run-1")
    assert ok
    html = open(os.path.join(run_dir, "index.html"), encoding="utf-8").read()
    assert "Advisory" in html              # advisory section rendered
    assert "TC-API-user-1" in html         # advisory TC still shown
    # headline reflects canonical 100, not dragged down by the advisory fails
    assert ">100<" in html or "100/100" in html or "100%" in html

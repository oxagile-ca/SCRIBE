import qa_scoring


class TestClassifyTc:
    def test_ac_tied_id_is_scoring(self):
        assert qa_scoring.classify_tc("TC-0675-001") == "scoring"

    def test_console_and_network_uv_are_scoring(self):
        assert qa_scoring.classify_tc("TC-UV-1") == "scoring"
        assert qa_scoring.classify_tc("TC-UV-2") == "scoring"

    def test_other_uv_are_advisory(self):
        for tc in ("TC-UV-3", "TC-UV-4", "TC-UV-5", "TC-UV-6"):
            assert qa_scoring.classify_tc(tc) == "advisory"

    def test_api_is_advisory(self):
        assert qa_scoring.classify_tc("TC-API-user-1") == "advisory"
        assert qa_scoring.classify_tc("tc-api-fees-3") == "advisory"

    def test_unknown_id_defaults_to_scoring(self):
        assert qa_scoring.classify_tc("WEIRD") == "scoring"
        assert qa_scoring.classify_tc("") == "scoring"


def _tc(tc_id, status):
    return {"id": tc_id, "status": status}


class TestComputeScore:
    def test_advisory_failures_do_not_lower_score(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-0675-002", "pass"),
               _tc("TC-UV-1", "pass"), _tc("TC-UV-2", "pass"),
               _tc("TC-UV-5", "fail"), _tc("TC-API-user-1", "fail")]
        s = qa_scoring.compute_score(tcs)
        assert s["pct"] == 100
        assert s["verdict"] == "PASS"
        assert s["total"] == 4  # advisory excluded from denominator

    def test_scoring_console_fail_downgrades(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-UV-1", "fail")]
        s = qa_scoring.compute_score(tcs)
        assert s["fail"] == 1
        assert s["pct"] == 50
        assert s["verdict"] == "FAIL"  # 50 < 60

    def test_scoring_fail_high_passrate_is_pass_with_issues(self):
        tcs = [_tc(f"TC-X-00{i}", "pass") for i in range(1, 9)] + [_tc("TC-X-009", "fail")]
        s = qa_scoring.compute_score(tcs)
        assert s["verdict"] == "PASS-WITH-ISSUES"  # 8/9 ≈ 89 ≥ 60

    def test_needs_review_blocks_clean_pass(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-RECON", "needs-review")]
        s = qa_scoring.compute_score(tcs)
        assert s["verdict"] == "PASS-WITH-ISSUES"

    def test_exempt_and_skipped_excluded_from_denominator(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-UV-4", "exempt"),
               _tc("TC-X-002", "skipped")]
        s = qa_scoring.compute_score(tcs)
        assert s["total"] == 1 and s["pct"] == 100 and s["verdict"] == "PASS"

    def test_empty_is_blocked(self):
        s = qa_scoring.compute_score([])
        assert s["total"] == 0 and s["verdict"] == "BLOCKED"

    def test_scoring_blocked_is_blocked(self):
        s = qa_scoring.compute_score([_tc("TC-0675-001", "blocked")])
        assert s["verdict"] == "BLOCKED"

    def test_split_preserves_order(self):
        tcs = [_tc("TC-0675-001", "pass"), _tc("TC-API-1", "pass"), _tc("TC-UV-2", "pass")]
        scored, advisory = qa_scoring.split_test_cases(tcs)
        assert [t["id"] for t in scored] == ["TC-0675-001", "TC-UV-2"]
        assert [t["id"] for t in advisory] == ["TC-API-1"]

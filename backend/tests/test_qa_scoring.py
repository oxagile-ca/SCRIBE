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

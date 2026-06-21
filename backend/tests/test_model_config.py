import importlib


def test_model_defaults():
    import config
    importlib.reload(config)  # ensure no leftover env override from another test
    assert config.CHEAP_MODEL == "claude-haiku-4-5"
    assert config.QA_EVIDENCE_MODEL == "claude-haiku-4-5"
    assert config.CHAT_MODEL == "claude-haiku-4-5"


def test_model_env_override(monkeypatch):
    import config
    monkeypatch.setenv("SCRIBE_QA_EVIDENCE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("SCRIBE_CHAT_MODEL", "claude-opus-4-8")
    importlib.reload(config)
    try:
        assert config.QA_EVIDENCE_MODEL == "claude-sonnet-4-6"
        assert config.CHAT_MODEL == "claude-opus-4-8"
    finally:
        # restore defaults so later tests see the unoverridden module
        monkeypatch.undo()
        importlib.reload(config)

"""Tests for version_info — the `be: <version>` stamp, with a non-git fallback so
packaged builds (no .git dir) stop showing a bare `unknown`."""
import version_info as vi


def test_fallback_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIBE_VERSION", "1.2.3")
    assert vi.fallback_version(str(tmp_path)) == "1.2.3"


def test_fallback_reads_bundled_version_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SCRIBE_VERSION", raising=False)
    (tmp_path / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    assert vi.fallback_version(str(tmp_path)) == "v9.9.9"


def test_fallback_unknown_when_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("SCRIBE_VERSION", raising=False)
    assert vi.fallback_version(str(tmp_path)) == "unknown"


def test_resolve_prefers_git_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(vi, "git_version", lambda root=None: "abc123+dirty")
    assert vi.resolve_version(str(tmp_path)) == "abc123+dirty"


def test_resolve_falls_back_when_git_unavailable(monkeypatch, tmp_path):
    # the packaged-app case: no git → fall back to the env/VERSION source, never "unknown"
    monkeypatch.setattr(vi, "git_version", lambda root=None: None)
    monkeypatch.setenv("SCRIBE_VERSION", "pkg-1.0.8")
    assert vi.resolve_version(str(tmp_path)) == "pkg-1.0.8"

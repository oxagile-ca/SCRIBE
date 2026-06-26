import os
import pdf_export


def test_build_chrome_args_has_headless_and_print_to_pdf():
    args = pdf_export.build_chrome_args(
        r"C:\chrome.exe", r"C:\ev\index.html", r"C:\ev\out.pdf"
    )
    assert args[0] == r"C:\chrome.exe"
    assert "--headless=new" in args
    assert "--print-to-pdf=C:\\ev\\out.pdf" in args
    # the source html is passed as a file:// URL, last arg
    assert args[-1].startswith("file:///")
    assert args[-1].endswith("index.html")


def test_find_browser_returns_path_or_none(monkeypatch):
    # When a known path exists, it is returned.
    monkeypatch.setattr(pdf_export.os.path, "exists", lambda p: p == pdf_export.CHROME_CANDIDATES[0])
    assert pdf_export.find_browser() == pdf_export.CHROME_CANDIDATES[0]
    # When none exist and no env override, returns None.
    monkeypatch.setattr(pdf_export.os.path, "exists", lambda p: False)
    monkeypatch.delenv("SCRIBE_CHROME_PATH", raising=False)
    assert pdf_export.find_browser() is None


def test_export_returns_none_when_no_browser(monkeypatch, tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html><body>hi</body></html>", encoding="utf-8")
    monkeypatch.setattr(pdf_export, "find_browser", lambda: None)
    import asyncio
    result = asyncio.run(pdf_export.export(str(html)))
    assert result is None


def test_find_browser_env_override(monkeypatch):
    monkeypatch.setenv("SCRIBE_CHROME_PATH", r"C:\custom\chrome.exe")
    monkeypatch.setattr(pdf_export.os.path, "exists", lambda p: p == r"C:\custom\chrome.exe")
    assert pdf_export.find_browser() == r"C:\custom\chrome.exe"

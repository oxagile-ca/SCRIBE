"""HTML -> PDF via headless Chrome. No external Python dependency.

The evidence report (index.html) is self-contained (base64-embedded images), so a
plain headless Chrome print-to-pdf renders it offline and faithfully.
"""
import asyncio
import os
from pathlib import Path

# Standard install paths on this Windows machine; Edge is a Chromium fallback.
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    """Return a usable Chromium binary path, or None. SCRIBE_CHROME_PATH overrides."""
    override = os.environ.get("SCRIBE_CHROME_PATH")
    if override and os.path.exists(override):
        return override
    for candidate in CHROME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def build_chrome_args(browser: str, html_path: str, pdf_path: str) -> list[str]:
    """Argv for a headless print-to-pdf. Last arg is the source as a file:// URL."""
    src_url = Path(html_path).resolve().as_uri()
    return [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        src_url,
    ]


async def export(html_path: str, pdf_path: str | None = None, timeout_s: int = 60) -> str | None:
    """Convert html_path -> pdf_path (default: sibling evidence.pdf). Returns the pdf
    path on success, None on any failure. Never raises — the caller degrades to HTML."""
    if not os.path.exists(html_path):
        return None
    browser = find_browser()
    if not browser:
        return None
    if pdf_path is None:
        pdf_path = os.path.join(os.path.dirname(html_path), "evidence.pdf")
    args = build_chrome_args(browser, html_path, pdf_path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None
    except Exception:
        return None
    if proc.returncode == 0 and os.path.exists(pdf_path):
        return pdf_path
    return None

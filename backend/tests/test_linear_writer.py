import asyncio
import linear_writer


def test_build_comment_markdown_includes_score_and_verdict():
    md = linear_writer.build_comment_markdown("INV-660", "/evidence/INV-660/runs/r/index.html", 94, "PASS")
    assert "INV-660" in md
    assert "94" in md
    assert "PASS" in md


def test_attach_skips_when_write_not_allowed():
    res = asyncio.run(linear_writer.attach_evidence(
        "INV-1", "x.pdf", "c", token="tok", write_allowed=False))
    assert res["attached"] is False
    assert res["skipped_reason"] and "write" in res["skipped_reason"].lower()


def test_attach_skips_when_no_token():
    res = asyncio.run(linear_writer.attach_evidence(
        "INV-1", "x.pdf", "c", token="", write_allowed=True))
    assert res["attached"] is False
    assert res["skipped_reason"] and "token" in res["skipped_reason"].lower()


def test_attach_skips_when_pdf_missing(tmp_path):
    res = asyncio.run(linear_writer.attach_evidence(
        "INV-1", str(tmp_path / "nope.pdf"), "c", token="tok", write_allowed=True))
    assert res["attached"] is False
    assert res["skipped_reason"] and "pdf" in res["skipped_reason"].lower()

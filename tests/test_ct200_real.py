from pathlib import Path

from app.parser import parse_pdf


ROOT = Path(__file__).resolve().parents[1]

V1_PDF = ROOT / "data" / "ct200_manual_v1.pdf"
V2_PDF = ROOT / "data" / "ct200_manual_v2.pdf"


def test_real_ct200_hierarchy_and_tables():
    """
    Verify that the real CT-200 PDF is parsed into meaningful
    headings, sections, and table content.
    """
    doc = parse_pdf(V1_PDF)

    headings = [node.heading for node in doc.nodes]

    assert "1. Device Overview" in headings
    assert "2.1 General Speciﬁcations" in headings
    assert "2.1.1.1 Battery Life Under Typical Use" in headings

    specifications = next(
        node
        for node in doc.nodes
        if node.heading.startswith("2.1 General")
    )

    error_codes = next(
        node
        for node in doc.nodes
        if node.heading == "4.2 Error Codes"
    )

    assert "[TABLE]" in specifications.body_text
    assert "Measurement method" in specifications.body_text

    assert "[TABLE]" in error_codes.body_text
    assert "E3" in error_codes.body_text


def test_real_ct200_v1_to_v2_changes():
    """
    Verify real version differences between CT-200 v1 and v2.

    The test checks:
    - sections added/removed between versions
    - stable logical identity for an existing section
    - changed content hash when the section content changes
    """
    v1 = parse_pdf(V1_PDF)
    v2 = parse_pdf(V2_PDF)

    v1_headings = {node.heading for node in v1.nodes}
    v2_headings = {node.heading for node in v2.nodes}

    # Data Export is present in v2.
    assert "5.3 Data Export" not in v1_headings
    assert "5.3 Data Export" in v2_headings

    # Find the same logical section in both versions.
    battery_v1 = next(
        node
        for node in v1.nodes
        if "Battery Life Under Typical Use" in node.heading
    )

    battery_v2 = next(
        node
        for node in v2.nodes
        if "Battery Life Under Typical Use" in node.heading
    )

    # The section represents the same logical node.
    assert battery_v1.logical_key == battery_v2.logical_key

    # Its actual content is different between versions.
    assert battery_v1.content_hash != battery_v2.content_hash

    # Both versions must contain real battery information.
    assert "measurement cycles" in battery_v1.body_text
    assert "low" in battery_v1.body_text.lower()

    assert "measurement cycles" in battery_v2.body_text
    assert "low" in battery_v2.body_text.lower()

    # Confirm the extracted values are represented in the actual PDFs.
    assert "300 measurement cycles" in battery_v1.body_text
    assert "15%" in battery_v1.body_text

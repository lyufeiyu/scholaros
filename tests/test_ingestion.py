from __future__ import annotations

import hashlib
import zipfile

import pytest

from scholaros.ingestion import DocumentIngestor


@pytest.mark.parametrize("suffix", [".txt", ".md", ".markdown", ".tex", ".bib", ".csv", ".json"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_text_import_removes_bom_and_preserves_content(tmp_path, suffix, encoding) -> None:
    path = tmp_path / f"reference{suffix}"
    text = "研究资料\nAlpha\tBeta\ufeffGamma"
    raw = text.encode(encoding)
    path.write_bytes(raw)

    result = DocumentIngestor().ingest(path)

    assert result.text == text
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.id == result.sha256[:16]


@pytest.mark.parametrize("text", ["", " \t\r\n"])
def test_bom_only_or_whitespace_document_is_rejected(tmp_path, text) -> None:
    path = tmp_path / "empty.txt"
    path.write_bytes(text.encode("utf-8-sig"))

    with pytest.raises(ValueError, match="资料没有可提取文本"):
        DocumentIngestor().ingest(path)


def test_docx_preserves_tabs_and_line_breaks(tmp_path) -> None:
    path = tmp_path / "formatted.docx"
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Alpha</w:t><w:tab/><w:t>Beta</w:t>"
        "<w:br/><w:t>Gamma</w:t><w:cr/><w:t>Delta</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Next paragraph</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)

    result = DocumentIngestor().ingest(path)

    assert result.text == "Alpha\tBeta\nGamma\nDelta\n\nNext paragraph"

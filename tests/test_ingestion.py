from __future__ import annotations

import zipfile

from scholaros.ingestion import DocumentIngestor


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

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class IngestedDocument:
    id: str
    name: str
    kind: str
    text: str
    page_count: int | None
    sha256: str

    def to_dict(self) -> dict[str, str | int | None]:
        return asdict(self)


class DocumentIngestor:
    allowed_suffixes = {".pdf", ".txt", ".md"}

    def ingest(self, path: Path) -> IngestedDocument:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"资料不存在：{path}")
        suffix = path.suffix.lower()
        if suffix not in self.allowed_suffixes:
            raise ValueError("仅支持 PDF、TXT 和 Markdown 资料")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if suffix == ".pdf":
            text, page_count = self._read_pdf(path)
        else:
            text = raw.decode("utf-8", errors="replace")
            page_count = None
        text = text.strip()
        if not text:
            raise ValueError("资料没有可提取文本；扫描 PDF 请先执行 OCR")
        return IngestedDocument(
            id=digest[:16],
            name=path.name,
            kind=suffix.removeprefix("."),
            text=text,
            page_count=page_count,
            sha256=digest,
        )

    @staticmethod
    def _read_pdf(path: Path) -> tuple[str, int]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("读取 PDF 需要安装 pypdf") from exc
        reader = PdfReader(path)
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return text, len(reader.pages)

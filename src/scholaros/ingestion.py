from __future__ import annotations

import hashlib
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree


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
    allowed_suffixes = {
        ".pdf",
        ".txt",
        ".md",
        ".markdown",
        ".tex",
        ".bib",
        ".csv",
        ".json",
        ".docx",
    }

    def ingest(self, path: Path) -> IngestedDocument:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"资料不存在：{path}")
        suffix = path.suffix.lower()
        if suffix not in self.allowed_suffixes:
            raise ValueError("仅支持 PDF、Word、TXT、Markdown、LaTeX、BibTeX、CSV 和 JSON 资料")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if suffix == ".pdf":
            text, page_count = self._read_pdf(path)
        elif suffix == ".docx":
            text = self._read_docx(path)
            page_count = None
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

    @staticmethod
    def _read_docx(path: Path) -> str:
        try:
            with zipfile.ZipFile(path) as archive:
                try:
                    info = archive.getinfo("word/document.xml")
                except KeyError as exc:
                    raise ValueError("Word 文件缺少正文结构") from exc
                if info.file_size > 20 * 1024 * 1024:
                    raise ValueError("Word 正文解压后不能超过 20 MiB")
                raw = archive.read(info)
        except zipfile.BadZipFile as exc:
            raise ValueError("Word 文件结构无效") from exc
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise ValueError("Word 正文 XML 无法解析") from exc
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(f"{namespace}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if text.strip():
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

"""Resume parser — PDF/DOCX → plain text. Implemented in Phase 2."""
import io


async def extract_text(data: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return await _parse_pdf(data)
    if ext in ("docx", "doc"):
        return await _parse_docx(data)
    raise ValueError(f"Unsupported file type: {ext}")


async def _parse_pdf(data: bytes) -> str:
    import asyncio

    import pypdf

    def _extract() -> str:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    return await asyncio.to_thread(_extract)


async def _parse_docx(data: bytes) -> str:
    import asyncio

    import docx

    def _extract() -> str:
        doc = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)

    return await asyncio.to_thread(_extract)

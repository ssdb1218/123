"""Extract uploaded PDF text in memory before handing it to the editor."""
from __future__ import annotations

import io

from pypdf import PdfReader


MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 20_000


class PdfImportError(ValueError):
    """An uploaded PDF cannot safely populate the text input."""


def extract_pdf_text(data: bytes) -> str:
    """Return page-ordered text without writing or caching the uploaded bytes."""
    if not data:
        raise PdfImportError("빈 파일입니다. 본문이 있는 PDF를 선택하세요.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfImportError("PDF 파일은 20MB 이하만 업로드할 수 있습니다.")

    try:
        with io.BytesIO(data) as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise PdfImportError("암호화된 PDF는 읽을 수 없습니다. 암호를 해제한 PDF를 업로드하세요.")

            pages = []
            text_length = 0
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                if not text:
                    continue
                text_length += len(text) + (2 if pages else 0)
                if text_length > MAX_TEXT_CHARS:
                    raise PdfImportError(
                        "추출한 본문이 20,000자를 초과합니다. PDF를 나누거나 필요한 부분만 직접 입력하세요."
                    )
                pages.append(text)
    except PdfImportError:
        raise
    except Exception as exc:
        raise PdfImportError("PDF를 읽을 수 없습니다. 정상적인 PDF 파일인지 확인하세요.") from exc

    if not pages:
        raise PdfImportError(
            "PDF에서 추출할 본문을 찾지 못했습니다. 스캔·이미지 PDF는 OCR로 변환한 뒤 업로드하세요."
        )
    return "\n\n".join(pages)

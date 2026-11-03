import io
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from textlab import pdf_import
from textlab.pdf_import import MAX_PDF_BYTES, MAX_TEXT_CHARS, PdfImportError, extract_pdf_text


@pytest.fixture(scope="module", autouse=True)
def korean_font():
    font = Path(__file__).parents[1] / "textlab" / "assets" / "NanumGothic-Regular.ttf"
    pdfmetrics.registerFont(TTFont("PdfImportTestKorean", str(font)))


def make_pdf(pages):
    output = io.BytesIO()
    canvas = Canvas(output)
    for lines in pages:
        canvas.setFont("PdfImportTestKorean", 12)
        for index, line in enumerate(lines):
            canvas.drawString(50, 780 - index * 20, line)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def test_extracts_korean_pages_in_order_and_preserves_line_breaks():
    pdf = make_pdf([["첫 번째 페이지 본문", "한국어와 English 123"], ["두 번째 페이지입니다."]])

    assert extract_pdf_text(pdf) == "첫 번째 페이지 본문\n한국어와 English 123\n\n두 번째 페이지입니다."


def test_blank_pages_do_not_hide_other_pages_text():
    pdf = make_pdf([[], ["읽을 수 있는 본문"], []])

    assert extract_pdf_text(pdf) == "읽을 수 있는 본문"


@pytest.mark.parametrize("data", [b"", b"This is not a PDF", b"%PDF-1.7\nbroken"])
def test_rejects_empty_invalid_or_damaged_pdf(data):
    with pytest.raises(PdfImportError, match="빈 파일|PDF를 읽을 수 없습니다"):
        extract_pdf_text(data)


def test_rejects_truncated_pdf():
    with pytest.raises(PdfImportError, match="PDF를 읽을 수 없습니다"):
        extract_pdf_text(make_pdf([["손상된 파일"]])[:100])


@pytest.mark.parametrize("password", ["secret", ""])
def test_rejects_encrypted_pdf_even_with_empty_password(password):
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(make_pdf([["암호화된 본문"]]))))
    writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)

    with pytest.raises(PdfImportError, match="암호화된 PDF"):
        extract_pdf_text(output.getvalue())


def test_rejects_pdf_with_no_pages():
    output = io.BytesIO()
    PdfWriter().write(output)

    with pytest.raises(PdfImportError, match="본문을 찾지 못했습니다"):
        extract_pdf_text(output.getvalue())


def test_rejects_blank_pdf():
    with pytest.raises(PdfImportError, match="본문을 찾지 못했습니다"):
        extract_pdf_text(make_pdf([[]]))


def test_rejects_image_only_pdf_with_ocr_guidance():
    output = io.BytesIO()
    canvas = Canvas(output)
    image = Image.new("RGB", (100, 100), "white")
    canvas.drawImage(ImageReader(image), 50, 500, width=100, height=100)
    canvas.save()

    with pytest.raises(PdfImportError, match="스캔·이미지 PDF는 OCR"):
        extract_pdf_text(output.getvalue())


def test_rejects_file_over_size_limit_before_parsing():
    with pytest.raises(PdfImportError, match="20MB 이하"):
        extract_pdf_text(b"x" * (MAX_PDF_BYTES + 1))


def test_accepts_file_at_size_limit(monkeypatch):
    pdf = make_pdf([["크기 경계값"]])
    monkeypatch.setattr(pdf_import, "MAX_PDF_BYTES", len(pdf))

    assert extract_pdf_text(pdf) == "크기 경계값"


def test_accepts_exact_character_limit():
    text = "가" * MAX_TEXT_CHARS

    assert extract_pdf_text(make_pdf([[text]])) == text


def test_rejects_text_over_character_limit_without_truncation():
    with pytest.raises(PdfImportError, match="20,000자를 초과"):
        extract_pdf_text(make_pdf([["가" * (MAX_TEXT_CHARS + 1)]]))


def test_character_limit_includes_page_separators():
    with pytest.raises(PdfImportError, match="20,000자를 초과"):
        extract_pdf_text(make_pdf([["가" * 10_000], ["나" * 10_000]]))

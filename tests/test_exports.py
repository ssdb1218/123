import io
import json

import pytest
from docx import Document
from pypdf import PdfReader

from textlab.exports import export_docx, export_json, export_pdf
from textlab.models import Candidate, Detection, Iteration, RunState, SemanticAssessment, Settings


@pytest.fixture
def sample_state():
    score = Detection(42.5, "MockDetector", True, "테스트용")
    candidate = Candidate(
        "1-1", "한국어 결과 & <태그> 123", ["중복 표현 제거"], True,
        Detection(30.0, "MockDetector", True), SemanticAssessment(0.95, True, "원문 의미 유지"),
        True, "사용자 승인 후 채택",
    )
    state = RunState("한국어 원문 & <태그> 123", Settings(), is_mock=True)
    state.initial = score
    state.current = state.best = candidate.detection
    state.current_text = state.best_text = candidate.text
    state.iterations = [Iteration(1, [candidate], 30.0, "1-1")]
    state.status = "completed"
    state.stop_reason = "max_iterations"
    state.usage = {"api_calls": 4, "reserved_cost_usd": 0.05}
    return state


def test_json_contains_full_audit(sample_state):
    report = json.loads(export_json(sample_state))
    assert report["score_mode"].startswith("MOCK")
    assert report["iterations"][0]["candidates"][0]["reason"] == "사용자 승인 후 채택"
    assert report["iterations"][0]["candidates"][0]["semantic"]["score"] == 0.95
    assert report["original_text"] == sample_state.original_text
    assert "보장하지 않습니다" in report["disclaimer"]


def test_docx_preserves_korean_and_literal_markup(sample_state):
    document = Document(io.BytesIO(export_docx(sample_state)))
    text = "\n".join(p.text for p in document.paragraphs)
    assert sample_state.original_text in text
    assert sample_state.best_text in text
    assert "MOCK" in text
    assert "사용자 승인 후 채택" in text
    assert "42.50%" in text
    assert "0.95" in text


def test_pdf_embeds_korean_and_escapes_markup(sample_state):
    pdf = export_pdf(sample_state)
    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "한국어 결과 & <태그> 123" in text
    assert "사용자 승인 후 채택" in text
    assert "MOCK" in text
    # TrueType font program is embedded, not a viewer-dependent CID font.
    assert b"/FontFile2" in pdf


def test_pdf_wraps_long_unbroken_lines_and_paginates(sample_state):
    sample_state.best_text = "긴문장" * 4000 + " UNBROKEN" * 1000
    reader = PdfReader(io.BytesIO(export_pdf(sample_state)))
    assert len(reader.pages) > 2
    assert "긴문장" in "".join(p.extract_text() for p in reader.pages)


def test_missing_pdf_font_is_actionable(sample_state, monkeypatch):
    monkeypatch.setenv("PDF_FONT_PATH", "/nonexistent/font.ttf")
    with pytest.raises(ValueError, match="PDF_FONT_PATH"):
        export_pdf(sample_state)


def test_exports_before_initial_detection(sample_state):
    sample_state.initial = sample_state.current = sample_state.best = None
    sample_state.iterations = []
    sample_state.status = "error"
    assert export_pdf(sample_state).startswith(b"%PDF-")
    assert export_docx(sample_state).startswith(b"PK")

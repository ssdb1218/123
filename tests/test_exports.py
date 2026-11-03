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


@pytest.fixture(params=[False, True], ids=["openai-editing", "mock-editing"])
def editing_state(sample_state, request):
    sample_state.editing_only = True
    sample_state.is_mock = request.param
    sample_state.initial = sample_state.current = sample_state.best = None
    sample_state.settings.max_iterations = 1
    sample_state.iterations[0].best_score = None
    sample_state.iterations[0].candidates[0].detection = None
    sample_state.stop_reason = "문장 개선을 완료했습니다."
    return sample_state


def assert_editing_report_text(text, state):
    assert state.original_text in text
    assert state.best_text in text
    assert "최종 본문" in text
    assert "AI 탐지 점수를 측정하지 않으며" in text
    assert "점수 감소·인간 작성 여부·탐지 회피를 보장하지 않습니다" in text
    assert "원문 의미 보존 평가 (0~1, AI 탐지와 별개): 0.95" in text
    assert "원문 의미 유지" in text
    for misleading in (
        "최초 탐지 점수", "현재 탐지 점수", "최저 탐지 점수", "최저 점수 채택본",
        "현재 최저 점수", "가상 점수", "외부 판독 API 응답", "탐지 결과:",
        "target_score", "detector_request_cost", "patience", "42.50%", "30.00%",
    ):
        assert misleading not in text


def test_editing_json_omits_detection_fields_and_preserves_separate_semantics(editing_state):
    before = editing_state.to_dict()

    report = json.loads(export_json(editing_state))

    assert editing_state.to_dict() == before
    assert report["editing_only"] is True
    assert report["final_text"] == editing_state.best_text
    assert report["original_text"] == editing_state.original_text
    assert "문장 개선" in report["mode"]
    assert "탐지 확률이 아닙니다" in report["disclaimer"]
    for key in ("initial", "current", "best", "score_mode"):
        assert key not in report
    for key in ("target_score", "detector_request_cost", "patience"):
        assert key not in report["settings"]
    iteration = report["iterations"][0]
    assert "best_score" not in iteration
    candidate = iteration["candidates"][0]
    assert "detection" not in candidate
    assert candidate["semantic"]["score"] == 0.95
    assert candidate["semantic"]["preserves_meaning"] is True
    assert candidate["reason"] == "사용자 승인 후 채택"


def test_editing_docx_has_final_text_and_no_detection_scores(editing_state):
    document = Document(io.BytesIO(export_docx(editing_state)))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert_editing_report_text(text, editing_state)
    assert "문장 개선 결과" in document.core_properties.title
    assert "문장 개선" in document.core_properties.subject
    assert "탐지" not in document.core_properties.subject


def test_editing_pdf_has_final_text_and_no_detection_scores(editing_state):
    pdf = export_pdf(editing_state)
    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)

    assert_editing_report_text(text, editing_state)
    assert "문장 개선 결과" in reader.metadata.title
    assert "서스데반의 AI 판독기" in text
    assert b"/FontFile2" in pdf


def test_editing_json_omits_pending_candidate_detection(editing_state):
    editing_state.pending_candidate = editing_state.iterations[0].candidates[0]

    report = json.loads(export_json(editing_state))

    assert "detection" not in report["pending_candidate"]
    assert report["pending_candidate"]["semantic"]["score"] == 0.95

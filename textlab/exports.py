"""Portable reports. Text is escaped, and Korean PDF glyphs are embedded."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from textlab.models import Detection, RunState


DISCLAIMER = (
    "AI 탐지 점수는 서비스의 확률적 추정치이며 실제 작성 주체를 증명하지 않습니다. "
    "목표 점수 또는 0% 달성을 보장하지 않습니다. 의미 보존 점수도 자동 평가의 추정치이므로 "
    "최종 문장의 사실관계는 사용자가 확인해야 합니다."
)
EDITING_DISCLAIMER = (
    "이 문장 개선 모드에서는 AI 탐지 점수를 측정하지 않으며, 점수 감소·인간 작성 여부·탐지 회피를 보장하지 않습니다. "
    "의미 보존 평가는 원문과 수정문의 의미 유지 여부에 대한 별도의 자동 평가이며 AI 탐지 확률이 아닙니다. "
    "자동 검사의 한계가 있으므로 최종 문장의 사실관계와 표현은 사용자가 확인해야 합니다."
)
_DETECTION_SETTINGS = {"target_score", "detector_request_cost", "patience"}
_FONT_LOCK = Lock()
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _clean(text: object) -> str:
    return _XML_INVALID.sub("", str(text))


def export_json(state: RunState) -> bytes:
    """Lossless machine-readable audit, including all rejected candidates."""
    payload = state.to_dict()
    if state.editing_only:
        payload["disclaimer"] = EDITING_DISCLAIMER
        payload["mode"] = "MOCK · 문장 개선 흐름 테스트" if state.is_mock else "OpenAI 문장 개선"
        payload["settings"] = _report_settings(state)
        payload["final_text"] = state.best_text or state.original_text
        for name in ("initial", "current", "best"):
            payload.pop(name, None)
        for iteration in payload["iterations"]:
            iteration.pop("best_score", None)
            for candidate in iteration["candidates"]:
                candidate.pop("detection", None)
        if payload["pending_candidate"] is not None:
            payload["pending_candidate"].pop("detection", None)
    else:
        payload["disclaimer"] = DISCLAIMER
        payload["score_mode"] = "MOCK · 테스트용 가상 점수" if state.is_mock else "외부 판독 API 응답"
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _report_settings(state: RunState) -> dict:
    settings = asdict(state.settings)
    if state.editing_only:
        for name in _DETECTION_SETTINGS:
            settings.pop(name, None)
    return settings


def _report_title(state: RunState) -> str:
    return "서스데반의 AI 판독기 · 문장 개선 결과" if state.editing_only else "AI 텍스트 탐지 및 문장 개선 결과"


def _detection(value: Detection | None) -> str:
    if value is None:
        return "측정되지 않음"
    mode = "MOCK · 테스트용 가상 점수" if value.is_mock else "외부 API 응답"
    return f"{value.score:.2f}% | {value.provider} | {mode}\n{value.explanation}"


def _sections(state: RunState):
    yield _report_title(state), ""
    if state.editing_only:
        yield "결과 해석과 한계", EDITING_DISCLAIMER
        yield "실행 모드", "MOCK · 문장 개선 흐름 테스트 (실제 모델 평가 아님)" if state.is_mock else "OpenAI 문장 개선 및 의미 검토"
    else:
        yield "점수의 의미와 한계", DISCLAIMER
        yield "실행 모드", "MOCK · 실제 AI 탐지 결과가 아닙니다." if state.is_mock else "외부 판독 API 응답 사용"
    yield "실행 정보", (
        f"실행 ID: {state.id}\n생성 시각 (UTC): {state.created_at}\n상태: {state.status}\n"
        f"종료 사유: {state.stop_reason}\n오류: {state.error or '없음'}\n반복 횟수: {len(state.iterations)}"
    )
    if not state.editing_only:
        yield "최초 탐지 점수", _detection(state.initial)
        yield "현재 탐지 점수", _detection(state.current)
        yield "최저 탐지 점수", _detection(state.best)
    yield "최종 본문" if state.editing_only else "최종 결과 (최저 점수 채택본)", state.best_text or state.original_text
    yield "원본 텍스트", state.original_text
    yield "현재 채택본", state.current_text or state.original_text
    yield "실행 설정", json.dumps(_report_settings(state), ensure_ascii=False, indent=2)
    yield "API 사용량 및 비용 예약", json.dumps(state.usage, ensure_ascii=False, indent=2)
    yield "원문 분석", json.dumps(state.analysis, ensure_ascii=False, indent=2)
    if state.pending_candidate is not None:
        yield "승인 대기 후보", state.pending_candidate.id
    for iteration in state.iterations:
        iteration_title = "개선" if state.editing_only else "반복"
        iteration_body = f"채택 후보 ID: {iteration.adopted_id or '없음 · 이전 버전 유지'}"
        if not state.editing_only:
            iteration_body = f"현재 최저 점수: {iteration.best_score if iteration.best_score is not None else '측정되지 않음'}\n" + iteration_body
        yield f"{iteration_title} {iteration.number}", iteration_body
        for candidate in iteration.candidates:
            semantic = candidate.semantic
            detection = "" if state.editing_only else f"탐지 결과: {_detection(candidate.detection)}\n"
            semantic_label = "원문 의미 보존 평가 (0~1, AI 탐지와 별개)" if state.editing_only else "의미 보존"
            yield f"후보 {candidate.id}", (
                f"채택 여부: {'채택' if candidate.accepted else '미채택'}\n"
                f"중요 수정: {'예' if candidate.major_change else '아니요'}\n"
                f"판정 사유: {candidate.reason}\n{detection}"
                f"{semantic_label}: {semantic.score if semantic else '미평가'}\n"
                f"의미 유지 판정: {semantic.preserves_meaning if semantic else '미평가'}\n"
                f"의미 평가 사유: {semantic.reason if semantic else '미평가'}\n"
                f"수정 내역:\n" + "\n".join(f"• {change}" for change in candidate.changes)
            )
            yield "후보 전문", candidate.text


def export_docx(state: RunState) -> bytes:
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "NanumGothic"
    normal.font.size = Pt(10)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "NanumGothic")
    # Let Korean and unbroken Latin strings wrap inside the page margins.
    compat = OxmlElement("w:compat")
    compat.append(OxmlElement("w:doNotUseEastAsianBreakRules"))
    document.settings.element.append(compat)
    for index, (title, body) in enumerate(_sections(state)):
        document.add_heading(_clean(title), level=0 if index == 0 else 2)
        if body:
            for line in _clean(body).split("\n"):
                document.add_paragraph(line)
    document.core_properties.title = _report_title(state)
    if state.editing_only:
        document.core_properties.subject = "MOCK 문장 개선 흐름 테스트" if state.is_mock else "OpenAI 문장 개선 및 의미 검토 기록"
    else:
        document.core_properties.subject = "MOCK 테스트 결과" if state.is_mock else "외부 API 탐지 기록"
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_font() -> str:
    configured = os.environ.get("PDF_FONT_PATH")
    path = Path(configured) if configured else Path(__file__).parent / "assets" / "NanumGothic-Regular.ttf"
    if not path.is_file():
        raise ValueError("PDF 한글 폰트가 없습니다. 배포 폰트를 복원하거나 PDF_FONT_PATH에 한글 TrueType 폰트를 지정하세요.")
    # Names differ for different user-supplied fonts, avoiding a process-global collision.
    name = "TextLab-" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    with _FONT_LOCK:
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
            except Exception as exc:
                raise ValueError("PDF_FONT_PATH는 ReportLab이 지원하는 한글 TrueType 폰트여야 합니다.") from exc
    return name


def export_pdf(state: RunState) -> bytes:
    font = _pdf_font()
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, leftMargin=44, rightMargin=44, topMargin=44, bottomMargin=44,
        title=_report_title(state), author="서스데반의 AI 판독기",
    )
    body_style = ParagraphStyle(
        "TextLabBody", fontName=font, fontSize=9, leading=15, wordWrap="CJK",
        alignment=TA_LEFT, splitLongWords=True, spaceAfter=5,
    )
    heading_style = ParagraphStyle(
        "TextLabHeading", parent=body_style, fontSize=12, leading=19,
        spaceBefore=12, spaceAfter=7, textColor=colors.HexColor("#15344b"), keepWithNext=True,
    )
    flowables = []
    for title, body in _sections(state):
        flowables.append(Paragraph(escape(_clean(title)), heading_style))
        if body:
            # Preserve line breaks without interpreting user text as ReportLab markup.
            for line in _clean(body).split("\n"):
                flowables.append(Paragraph(escape(line) or "<br/>", body_style))
        else:
            flowables.append(Spacer(1, 4))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        mode = "MOCK 문장 개선" if state.is_mock else "OpenAI 문장 개선"
        if not state.editing_only:
            mode = "MOCK 테스트" if state.is_mock else "API 관측"
        canvas.drawString(44, 25, f"서스데반의 AI 판독기 | {mode} | {doc.page}")
        canvas.restoreState()

    document.build(flowables, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()

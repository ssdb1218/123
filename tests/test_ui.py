from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path

from streamlit.testing.v1 import AppTest

from textlab.jobs import Worker
from textlab.models import Candidate, Detection, RunState, Settings


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def button(at, label):
    return next(item for item in at.button if item.label == label)


def pdf_upload(pages):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    font = Path(APP).parent / "textlab" / "assets" / "NanumGothic-Regular.ttf"
    font_name = "UiPdfImportKorean"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font)))
    output = io.BytesIO()
    canvas = Canvas(output)
    for page in pages:
        canvas.setFont(font_name, 12)
        canvas.drawString(50, 750, page)
        canvas.showPage()
    canvas.save()
    return "source.pdf", output.getvalue(), "application/pdf"


def test_pdf_upload_populates_editor_and_is_used_by_mock_analysis(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pages = ["기술은 일상을 바꿉니다.", "그러나 책임 있는 활용이 중요합니다."]
    expected = "\n\n".join(pages)
    at = AppTest.from_file(APP, default_timeout=10).run()

    at.file_uploader(key="source_pdf").set_value(pdf_upload(pages)).run()

    assert not at.exception
    assert at.text_area(key="source_text").value == expected
    assert any("추출했습니다" in item.value for item in at.success)
    assert "worker" not in at.session_state
    assert not button(at, "시작").disabled
    button(at, "시작").click().run()
    worker = at.session_state["worker"]
    worker._thread.join(timeout=5)
    assert not worker.is_alive
    assert worker.snapshot().original_text == expected
    assert worker.snapshot().initial.is_mock
    assert not worker.snapshot().error


def test_pdf_reruns_preserve_edits_and_same_file_can_be_selected_again(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    uploaded = pdf_upload(["PDF에서 가져온 원문입니다."])
    at = AppTest.from_file(APP).run()
    at.file_uploader(key="source_pdf").set_value(uploaded).run()
    assert at.text_area(key="source_text").value == "PDF에서 가져온 원문입니다."

    edited = "업로드 뒤 사용자가 직접 수정한 본문입니다."
    at.text_area(key="source_text").set_value(edited).run()
    at.run()
    assert not at.exception
    assert at.text_area(key="source_text").value == edited

    at.file_uploader(key="source_pdf").clear().run()
    assert not at.exception
    assert at.text_area(key="source_text").value == edited
    assert not at.success

    at.file_uploader(key="source_pdf").set_value(uploaded).run()
    assert not at.exception
    assert at.text_area(key="source_text").value == "PDF에서 가져온 원문입니다."
    assert any("추출했습니다" in item.value for item in at.success)


def test_pdf_upload_error_preserves_existing_text_and_clears_success(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP).run()
    at.file_uploader(key="source_pdf").set_value(pdf_upload(["정상 PDF 본문입니다."])).run()
    edited = "읽기 실패 후에도 유지해야 할 사용자 본문입니다."
    at.text_area(key="source_text").set_value(edited).run()

    at.file_uploader(key="source_pdf").set_value(("broken.pdf", b"not a PDF", "application/pdf")).run()

    assert not at.exception
    assert at.text_area(key="source_text").value == edited
    assert any("PDF를 읽을 수 없습니다" in item.value for item in at.error)
    assert not at.success
    assert not button(at, "시작").disabled
    assert "worker" not in at.session_state


def test_ui_defaults_and_complete_offline_workflow(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP, default_timeout=10).run()
    assert not at.exception
    values = {item.label: item.value for item in at.number_input}
    assert values["목표 AI 탐지 점수 (%)"] == 5.0
    assert values["최대 반복 횟수"] == 20
    assert values["반복당 후보"] == 3
    assert at.multiselect[0].value == ["PDF", "DOCX"]
    assert next(item for item in at.checkbox if item.label == "중요 수정은 채택 전 사용자 승인").value
    assert button(at, "시작").disabled
    at.text_area(key="source_text").set_value("기술은 일상을 바꿉니다. 그러나 책임 있는 활용이 중요합니다.").run()
    button(at, "시작").click().run()
    worker = at.session_state["worker"]
    worker._thread.join(timeout=5)
    state = worker.snapshot()
    assert not worker.is_alive
    assert state.status == "completed"
    assert state.initial.is_mock
    assert not state.error
    at.run()
    assert not at.exception
    assert len(at.get("download_button")) == 3
    assert any("Mock" in item.value for item in at.warning)
    assert any("최초 점수" in item.label and item.value != "—" for item in at.metric)
    assert len(list((tmp_path / ".runs").glob("*.json"))) == 1


def test_live_requires_consent_and_configuration_without_mock_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("LLM_API_KEY", "OPENAI_API_KEY", "GPTZERO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(APP).run()
    at.radio(key="mode").set_value("실제 API").run()
    at.text_area(key="source_text").set_value("실제 검사를 위한 텍스트입니다.").run()
    button(at, "시작").click().run()
    assert any("동의" in item.value for item in at.error)
    assert "worker" not in at.session_state
    at.checkbox(key="external_consent").check().run()
    button(at, "시작").click().run()
    assert any("LLM_API_KEY" in item.value for item in at.error)
    assert "worker" not in at.session_state
    assert not at.exception


def test_approval_ui_queues_exact_candidate_once_and_escapes_text(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP).run()
    worker = Worker("원본", Settings(), "mock")
    candidate = Candidate("1-2", '<script>alert("x")</script>수정', ["문장 흐름 개선"], True,
                          Detection(10, "mock", True))
    state = RunState("원본", Settings(), current_text="원본", best_text="원본", is_mock=True,
                     status="awaiting_approval", pending_candidate=candidate)
    worker._publish(state)
    at.session_state["worker"] = worker
    at.run()
    assert not at.exception
    assert any("&lt;script&gt;" in item.value for item in at.markdown)
    button(at, "수정 승인").click().run()
    assert worker.control.approvals.get_nowait() == ("1-2", True)
    assert not worker.respond("1-2", False)
    assert not worker.respond("unknown", True)


def test_worker_stop_cancels_pending_request_and_keeps_snapshot_isolated(monkeypatch, tmp_path):
    import textlab.providers as providers

    monkeypatch.chdir(tmp_path)
    started = threading.Event()

    class SlowDetector:
        is_mock = True

        async def detect(self, text):
            started.set()
            await asyncio.sleep(20)
            return Detection(80, "mock", True)

    class Budget:
        def snapshot(self):
            return {"api_calls": 0}

    class Client:
        closed = False

        async def aclose(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(providers, "build_providers", lambda mode, settings: (SlowDetector(), object(), Budget(), client))
    settings = Settings()
    worker = Worker("중지 검증 원문", settings, "mock")
    settings.max_iterations = 1
    assert worker.snapshot().settings.max_iterations == 20
    worker.start()
    assert started.wait(timeout=3)
    worker.stop()
    worker._thread.join(timeout=3)
    assert not worker.is_alive
    assert worker.snapshot().status == "stopped"
    assert client.closed
    detached = worker.snapshot()
    detached.original_text = "외부 수정"
    assert worker.snapshot().original_text == "중지 검증 원문"


def test_export_cache_is_session_local_reused_and_invalidated(monkeypatch, tmp_path):
    import textlab.exports as exports

    monkeypatch.chdir(tmp_path)
    generated = {"pdf": 0, "docx": 0}

    def fake_pdf(state):
        generated["pdf"] += 1
        return b"%PDF-test"

    def fake_docx(state):
        generated["docx"] += 1
        return b"docx-test"

    monkeypatch.setattr(exports, "export_pdf", fake_pdf)
    monkeypatch.setattr(exports, "export_docx", fake_docx)
    worker = Worker("원본", Settings(), "mock")
    detection = Detection(20, "mock", True)
    state = RunState("원본", Settings(), current_text="원본", best_text="원본", is_mock=True,
                     status="completed", initial=detection, current=detection, best=detection)
    worker._publish(state)

    first = AppTest.from_file(APP).run()
    first.session_state["worker"] = worker
    first.run()
    assert not first.exception
    assert generated == {"pdf": 1, "docx": 1}
    first.run()
    assert generated == {"pdf": 1, "docx": 1}

    # A second browser session has its own cache even for the same run payload.
    second = AppTest.from_file(APP).run()
    second.session_state["worker"] = worker
    second.run()
    assert generated == {"pdf": 2, "docx": 2}

    # Same run id with changed result text cannot receive stale report bytes.
    state.best_text = "변경된 원본"
    worker._publish(state)
    first.run()
    assert generated == {"pdf": 3, "docx": 3}
    assert len(first.session_state["_export_cache"]) == 2


def test_website_navigation_sample_and_empty_state(monkeypatch, tmp_path):
    from textlab.site import SAMPLE_TEXT

    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP).run()
    assert not at.exception
    page_markup = "\n".join(item.value for item in at.markdown)
    for section in ("workspace", "how-it-works", "questions"):
        assert f'href="#{section}"' in page_markup
        assert f'id="{section}"' in page_markup
    assert 'aria-label="주 메뉴"' in page_markup
    assert "첫 문장부터 시작해 볼까요?" in page_markup
    button(at, "예시 글 불러오기").click().run()
    assert at.text_area(key="source_text").value == SAMPLE_TEXT
    assert "worker" not in at.session_state
    assert not button(at, "시작").disabled
    assert any("실제 AI 판독 결과가 아닙니다" in item.value for item in at.warning)


def test_privacy_copy_matches_disabled_server_history(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEXTLAB_SAVE_HISTORY", "0")
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert any("실행 기록 파일을 남기지 않도록" in item.value for item in at.markdown)

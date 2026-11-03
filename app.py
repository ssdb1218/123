from __future__ import annotations

import difflib
import hashlib
import html
import json
import os

import streamlit as st
from dotenv import load_dotenv

from textlab.jobs import Worker
from textlab.models import RunState, Settings
from textlab.pdf_import import MAX_TEXT_CHARS, PdfImportError, extract_pdf_text
from textlab.site import SAMPLE_TEXT, install_theme, render_empty_state, render_guide, render_hero
from textlab.web_runtime import audit_directory, require_site_access

load_dotenv(override=False)
st.set_page_config(page_title="서스데반의 AI 판독기", page_icon="✍️", layout="wide")
require_site_access()
install_theme()


def import_source_pdf() -> None:
    uploaded = st.session_state.get("source_pdf")
    st.session_state.pop("_pdf_import_message", None)
    if uploaded is None:
        return
    try:
        with st.spinner("PDF에서 본문을 추출하고 있습니다…"):
            extracted = extract_pdf_text(uploaded.getvalue())
    except PdfImportError as exc:
        st.session_state["_pdf_import_message"] = ("error", str(exc))
        return
    # Upload callbacks run before the text widget is rendered. Update only on a
    # new upload so subsequent reruns preserve the user's edits to the text.
    st.session_state["source_text"] = extracted
    st.session_state["_pdf_import_message"] = (
        "success", f"PDF에서 {len(extracted):,}자를 추출했습니다. 아래 원본 텍스트를 확인하고 시작하세요."
    )


def render_diff(before: str, after: str) -> None:
    """Escape every input character before adding the small fixed HTML wrapper."""
    left: list[str] = []
    right: list[str] = []
    for tag, i, j, a, b in difflib.SequenceMatcher(None, before, after).get_opcodes():
        old, new = html.escape(before[i:j]), html.escape(after[a:b])
        left.append(old if tag == "equal" else f'<mark style="background:#ffe4e1">{old}</mark>')
        right.append(new if tag == "equal" else f'<mark style="background:#dcfce7">{new}</mark>')
    for col, label, chunks in zip(st.columns(2), ("수정 전", "수정 후"), (left, right)):
        with col:
            st.caption(label)
            st.markdown('<div class="tl-diff" tabindex="0" aria-label="' + label + ' 텍스트">'
                        + "".join(chunks) + "</div>", unsafe_allow_html=True)


def render_history(state: RunState) -> None:
    for iteration in reversed(state.iterations):
        with st.expander(f"{iteration.number}회차"):
            for candidate in iteration.candidates:
                st.markdown(f"**후보 {candidate.id} · {'채택' if candidate.accepted else '미채택'}**")
                st.caption("의미 보존 검토: "
                           + (("통과" if candidate.semantic.preserves_meaning
                               and candidate.semantic.score >= state.settings.min_semantic_score
                               else "기준 미달") if candidate.semantic else "미평가")
                           + (" · 중요 수정" if candidate.major_change else ""))
                if candidate.reason:
                    st.write(candidate.reason)
                if candidate.semantic:
                    st.caption(candidate.semantic.reason)
                for change in candidate.changes:
                    st.write("• " + change)
                st.text(candidate.text)


def export_files(state: RunState, formats: tuple[str, ...]) -> dict[str, bytes]:
    from textlab.exports import export_docx, export_json, export_pdf

    # Reports contain raw input. Keep a single report bundle in this session,
    # never Streamlit's process-wide data cache shared by unrelated sessions.
    fingerprint = hashlib.sha256(json.dumps(state.to_dict(), ensure_ascii=False,
                                           sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    key = (fingerprint, formats)
    cached = st.session_state.get("_export_cache")
    if cached is not None and cached[0] == key:
        return cached[1]
    result = {"json": export_json(state)}
    if "PDF" in formats:
        result["pdf"] = export_pdf(state)
    if "DOCX" in formats:
        result["docx"] = export_docx(state)
    st.session_state["_export_cache"] = (key, result)
    return result


render_hero()

with st.sidebar:
    st.header("내 글에 맞는 설정")
    st.caption("시작 전에 설정하세요. 실행 중에는 시작 시점의 기준을 사용합니다.")
    mode_label = st.radio("실행 모드", ["테스트용 Mock", "실제 API"], key="mode")
    mode = "mock" if mode_label == "테스트용 Mock" else "live"
    if mode == "mock":
        st.caption("API 키 없이 실행 흐름을 체험하는 모의 모드입니다.")
    else:
        st.info("OpenAI로 문장 개선과 원문 의미 검토를 수행합니다. AI 탐지 점수는 측정하지 않습니다.")
        st.caption("LLM_API_KEY: " + ("설정됨" if os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") else "미설정"))
    iterations = st.number_input("최대 개선 회차", min_value=1, max_value=5, value=1)
    candidates = st.number_input("회차당 후보", min_value=1, max_value=5, value=3)
    with st.expander("의미 보존·승인 기준", expanded=True):
        min_semantic = st.slider("최소 의미 보존 평가", 0.0, 1.0, 0.90, 0.01,
                                 help="원문과 후보의 의미 보존을 검토하는 모델 평가 기준입니다. AI 탐지 점수가 아닙니다.")
        require_approval = st.checkbox("중요 수정은 채택 전 사용자 승인", value=True)
    with st.expander("보호 및 비용 설정"):
        threshold = st.slider("중요 수정 분류 기준 (변경 비율)", 0.0, 1.0, 0.35, 0.05)
        terms = st.text_area("변경 금지 고유명사·전문 용어 (줄마다 하나)", help="수치·인용문은 자동 검사합니다. 고유명사는 여기에 명시하면 문자열 기준으로 보호합니다.")
        max_calls = st.number_input("총 API 요청 한도 (재시도 포함)", min_value=1, max_value=2000, value=200)
        max_cost = st.number_input("예상 비용 한도 (USD)", min_value=0.01, value=5.0, step=0.5)
        llm_cost = st.number_input("LLM 요청당 보수적 예상 비용 (USD)", min_value=0.0, value=0.02, format="%.4f")
        st.caption("한도는 입력한 요청당 예상액을 호출 전에 예약해 적용합니다. 실제 청구액이 아니며 서비스 요금·토큰량에 맞춰 높게 설정하세요. 재시도도 요청으로 계산합니다.")
    output_formats = st.multiselect("다운로드 형식", ["PDF", "DOCX"], default=["PDF", "DOCX"])
    st.caption("전체 수정 이력은 JSON으로도 내려받을 수 있습니다.")

settings = Settings(
    max_iterations=iterations, candidates_per_iteration=candidates,
    min_semantic_score=min_semantic, require_approval=require_approval,
    major_change_threshold=threshold, protected_terms=[term.strip() for term in terms.splitlines() if term.strip()],
    max_api_calls=max_calls, max_cost_usd=max_cost,
    detector_request_cost=0, llm_request_cost=llm_cost,
)

st.markdown('<section id="workspace" aria-label="글 다듬기"><p class="tl-section-label">YOUR WORKSPACE</p>'
            '<h2 class="tl-section-title">어떤 글을 다듬어 볼까요?</h2>'
            '<p class="tl-section-subtitle">원문을 붙여 넣거나 PDF에서 본문을 가져오세요. 수치·인용문은 보호하고, 중요한 수정은 먼저 확인합니다.</p></section>',
            unsafe_allow_html=True)
if mode == "mock":
    st.warning("테스트용 Mock 모드 · 공백·줄바꿈만 조정하는 체험 모드입니다. 실제 OpenAI 문장 개선을 실행하지 않습니다.")
else:
    st.info("실제 API 모드 · OpenAI로 자연스러움과 가독성을 개선합니다. 실행 전 글 전송에 동의해 주세요.")
st.caption("AI 탐지 점수는 측정하지 않으며 점수 하락·탐지 회피·인간 작성 여부를 보장하지 않습니다.")


def load_sample() -> None:
    st.session_state["source_text"] = SAMPLE_TEXT


sample_cols = st.columns([4, 1.5], vertical_alignment="center")
sample_cols[0].caption("아직 준비된 글이 없다면 예시로 먼저 살펴보세요.")
sample_cols[1].button("예시 글 불러오기", on_click=load_sample, key="load_sample",
                      disabled=bool(st.session_state.get("worker") and st.session_state.worker.is_alive),
                      use_container_width=True)
st.file_uploader(
    "PDF에서 본문 가져오기", type=["pdf"], key="source_pdf", on_change=import_source_pdf,
    help="최대 20MB · 추출 본문 최대 20,000자. 업로드하면 아래 입력 내용을 바꿉니다. "
         "텍스트가 포함된 PDF를 지원하며 스캔 이미지의 OCR은 지원하지 않습니다.",
)
if message := st.session_state.get("_pdf_import_message"):
    level, detail = message
    if level == "error":
        st.error(detail)
    else:
        st.success(detail)


text = st.text_area("원본 텍스트", height=250, max_chars=MAX_TEXT_CHARS,
                    placeholder="여기에 글을 붙여 넣어 주세요.\n\n원문의 핵심 의미와 문체를 유지하며 흐름과 표현을 다듬습니다.", key="source_text")
st.caption(f"{len(text):,} / 20,000자 · 개인정보나 비밀정보는 입력하지 마세요.")
consent = False
if mode == "live":
    consent = st.checkbox("입력 글과 수정 후보를 OpenAI에 전송하는 데 동의합니다.", key="external_consent")
    st.caption("비밀정보를 입력하지 마세요. 실제 API 모드는 키가 없거나 요청에 실패하면 중단하며 Mock으로 전환하지 않습니다.")


@st.fragment(run_every=0.5)
def workspace() -> None:
    worker: Worker | None = st.session_state.get("worker")
    running = bool(worker and worker.is_alive)
    cols = st.columns([1, 1, 4])
    if cols[0].button("시작", type="primary", disabled=running or not text.strip(), use_container_width=True):
        try:
            settings.validate()
            if mode == "live" and not consent:
                raise ValueError("실제 API 실행 전 외부 서비스 전송에 동의해 주세요.")
            if mode == "live" and not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
                raise ValueError("문장 개선 API가 설정되지 않았습니다. 사이트 운영자에게 LLM_API_KEY 설정을 요청해 주세요.")
            new_worker = Worker(text.strip(), settings, mode)
            new_worker.start()
            worker = new_worker
            st.session_state.worker = worker
            running = True
        except ValueError as exc:
            st.error(str(exc))
    if cols[1].button("중지", disabled=not running, use_container_width=True):
        worker.stop()
        st.info("중지를 요청했습니다. 진행 중 요청 정리 후 마지막으로 채택한 본문을 유지합니다.")
    if not worker:
        render_empty_state()
        return

    state = worker.snapshot()
    labels = {"initializing": "문장 개선 준비 중", "running": "문장 개선·의미 검토 중", "awaiting_approval": "중요 수정 승인 대기",
              "completed": "문장 개선 완료", "stopped": "중지됨", "error": "오류로 종료"}
    st.subheader(labels.get(state.status, state.status))
    if state.is_mock:
        st.warning("이 실행은 Mock 체험입니다. 실제 OpenAI 문장 개선이나 AI 판독을 수행하지 않습니다.")
    st.caption(f"실행 {state.id[:8]} · 최대 {state.settings.max_iterations}회 · "
               f"후보 {state.settings.candidates_per_iteration}개 · 입력과 설정은 시작 시점 기준")
    metrics = st.columns(3)
    metrics[0].metric("개선 회차", f"{len(state.iterations)} / {state.settings.max_iterations}")
    metrics[1].metric("채택한 수정", sum(candidate.accepted for iteration in state.iterations for candidate in iteration.candidates))
    metrics[2].metric("현재 글자 수", f"{len(state.best_text or state.original_text):,}")
    progress_slot = st.empty()
    if running:
        progress_slot.progress(min(len(state.iterations) / state.settings.max_iterations, 1.0),
                    text="원문 의미와 보호 조건을 검토하고 있습니다." if state.status != "awaiting_approval" else "중요 수정 확인을 기다리고 있습니다.")
    if state.usage:
        with st.expander("API 사용량 및 예약 비용"):
            st.write(f"사용한 요청: {state.usage.get('api_calls', 0)}회 · 예약 비용: ${state.usage.get('reserved_cost_usd', 0):.4f}")
            st.caption("예약 비용은 설정한 요청당 예상액의 합계로 실제 청구액과 다를 수 있습니다. 재시도도 요청 수에 포함됩니다.")
    if state.error:
        st.error(state.error)
    if state.stop_reason:
        st.info(state.stop_reason)
    if state.pending_candidate:
        pending = state.pending_candidate
        st.warning("중요 수정 후보입니다. 내용을 확인한 뒤 채택 여부를 결정하세요.")
        st.write(f"후보 {pending.id} · 수정 내용을 확인해 주세요.")
        render_diff(state.current_text or state.original_text, pending.text)
        for change in pending.changes:
            st.write("• " + change)
        answered = worker.has_responded(pending.id)
        a, b, _ = st.columns([1, 1, 4])
        if a.button("수정 승인", key=f"approve-{pending.id}", disabled=answered):
            worker.respond(pending.id, True)
            st.success("승인을 전달했습니다.")
        if b.button("후보 거절", key=f"reject-{pending.id}", disabled=answered):
            worker.respond(pending.id, False)
            st.info("거절을 전달했습니다. 이전 버전을 유지합니다.")
    tabs = st.tabs(["텍스트 비교", "수정 이력", "원문 분석", "다운로드"])
    with tabs[0]:
        render_diff(state.original_text, state.best_text or state.original_text)
    with tabs[1]:
        render_history(state)
    with tabs[2]:
        if state.analysis:
            stats = state.analysis
            analysis_cols = st.columns(3)
            analysis_cols[0].metric("원문 글자 수", f"{stats.get('characters', 0):,}")
            analysis_cols[1].metric("문장 수", stats.get("sentences", 0))
            analysis_cols[2].metric("평균 문장 길이", f"{stats.get('mean_sentence_length', 0):g}자")
            st.write(f"어휘 다양성: {stats.get('vocabulary_diversity', 0):.1%}")
            if stats.get("repeated_words"):
                st.caption("반복 표현")
                st.dataframe([{"표현": word, "횟수": count} for word, count in stats["repeated_words"].items()], hide_index=True, use_container_width=True)
            st.caption(stats.get("note", "단순 통계로 문장의 정확성을 보장하지 않습니다."))
        else:
            st.caption("분석 대기 중")
    with tabs[3]:
        if running:
            st.caption("실행을 마치거나 중지하면 현재까지의 결과와 전체 이력을 다운로드할 수 있습니다.")
        elif state.status == "error" and not any(candidate.accepted for iteration in state.iterations for candidate in iteration.candidates):
            st.caption("문장 개선 결과가 없습니다. API 설정과 오류 메시지를 확인하세요.")
        else:
            try:
                files = export_files(state, tuple(output_formats))
                for extension, content in files.items():
                    mime = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "json": "application/json"}[extension]
                    st.download_button(f"{extension.upper()} 다운로드", content,
                                       file_name=f"textlab-{state.id[:8]}.{extension}", mime=mime,
                                       help=f"{extension.upper()} 다운로드",
                                       on_click="ignore",
                                       key=f"download-{state.id}-{extension}")
                st.caption("원문, 최종 채택본, 설정, 의미 검토와 수정 이력이 포함됩니다. AI 탐지 점수는 포함하지 않습니다.")
            except Exception:
                st.error("파일을 생성하지 못했습니다. PDF용 한글 글꼴 및 내보내기 의존성을 확인해 주세요.")


workspace()
render_guide(history_enabled=audit_directory() is not None)

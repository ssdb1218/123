"""Local, accessible presentation components for the TextLab website."""
from __future__ import annotations

import streamlit as st


SAMPLE_TEXT = (
    "도시의 작은 도서관은 책을 빌리는 장소이면서 이웃이 만나는 공간이다. "
    "독서 모임과 글쓰기 수업은 서로 다른 관심사를 가진 주민들이 자연스럽게 대화하도록 돕는다. "
    "다만 프로그램을 늘리는 것만으로 참여가 활발해지는 것은 아니다. "
    "주민의 생활 시간과 이동 여건을 살피고, 조용히 책을 읽을 자리도 함께 확보할 필요가 있다."
)


def install_theme() -> None:
    st.markdown("""
<style>
:root{--tl-ink:#18352f;--tl-muted:#5a6e66;--tl-teal:#136c5b;--tl-line:#dce5dd;--tl-paper:#fff}
html{scroll-behavior:smooth;scroll-padding-top:30px}
[data-testid="stAppViewContainer"]{background:#f8faf7}
[data-testid="stMainBlockContainer"]{max-width:1210px;padding-top:4.3rem;padding-bottom:3rem}
[data-testid="stSidebar"]{background:#eef3ee;border-right:1px solid var(--tl-line)}
[data-testid="stSidebar"] h2{font-size:1.35rem}
[data-testid="stHeader"]{background:rgba(248,250,247,.94)}
[data-testid="stTextArea"] textarea{border-radius:14px;line-height:1.85;font-size:1rem}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button{min-height:44px;border-radius:10px;font-weight:650}
[data-testid="stButton"] button[kind="primary"]{background:var(--tl-teal);border-color:var(--tl-teal)}
[data-testid="stMetric"]{padding:1rem 1.15rem;border:1px solid var(--tl-line);background:#fff;border-radius:14px}
[data-testid="stMetricValue"]{color:var(--tl-ink);font-variant-numeric:tabular-nums}
[data-testid="stTabs"] [role="tablist"]{gap:1.5rem;border-bottom:1px solid var(--tl-line)}
[data-testid="stTabs"] [role="tab"]{min-height:46px;font-weight:650}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #218975!important;outline-offset:3px}
.tl-nav{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.25rem 0 1.35rem;border-bottom:1px solid var(--tl-line)}
.tl-brand{display:flex;align-items:center;gap:.6rem;color:var(--tl-ink)!important;font-size:1.22rem;font-weight:800;letter-spacing:-.04em;text-decoration:none!important}
.tl-mark{display:grid;place-items:center;width:32px;height:32px;background:var(--tl-teal);color:#fff;border-radius:9px;font-size:1.1rem}
.tl-nav-links{display:flex;align-items:center;gap:1.45rem;font-size:.88rem}
.tl-nav-links a{color:var(--tl-muted)!important;text-decoration:none!important;font-weight:600}
.tl-nav-links a:hover{text-decoration:underline!important}
.tl-hero{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(260px,1fr);gap:3.8rem;align-items:center;padding:3.3rem 0 2.7rem}
.tl-eyebrow{color:var(--tl-teal);font-size:.78rem;font-weight:750;letter-spacing:.1em;margin:0 0 1rem}
.tl-hero h1{color:var(--tl-ink);font-size:clamp(2.15rem,4vw,3.3rem);font-weight:750;letter-spacing:-.065em;line-height:1.27;margin:0 0 1.15rem;word-break:keep-all}
.tl-hero h1 span{color:var(--tl-teal)}
.tl-lead{color:var(--tl-muted);font-size:1.02rem;line-height:1.85;word-break:keep-all;margin:0 0 1.6rem}
.tl-cta{display:inline-flex;align-items:center;gap:1.3rem;background:var(--tl-teal);color:white!important;border-radius:10px;padding:.8rem 1.2rem;font-size:.94rem;font-weight:650;text-decoration:none!important}
.tl-hero-note{color:var(--tl-muted);font-size:.78rem;margin-top:.9rem;line-height:1.7}
.tl-process{background:#e8f0e7;border:1px solid #d5e2d4;border-radius:22px;padding:1.6rem 1.7rem;box-shadow:9px 10px 0 #f0f3ed}
.tl-process-title{font-size:.79rem;color:var(--tl-muted);margin:0 0 1rem;font-weight:650;letter-spacing:.03em}
.tl-process-step{display:flex;gap:.9rem;padding:.85rem 0;border-top:1px solid #d3dfd3;align-items:flex-start}
.tl-process-step b{color:var(--tl-teal);font-size:.75rem;padding-top:.2rem;font-variant-numeric:tabular-nums}
.tl-process-step strong{display:block;color:var(--tl-ink);font-size:.93rem;margin-bottom:.2rem}
.tl-process-step p{color:var(--tl-muted);font-size:.8rem;line-height:1.6;margin:0;word-break:keep-all}
.tl-section-label{font-size:.72rem;font-weight:750;color:var(--tl-teal);letter-spacing:.1em;margin:1.2rem 0 .5rem}
.tl-section-title{font-size:1.65rem;font-weight:730;letter-spacing:-.055em;color:var(--tl-ink);margin:0 0 .4rem}
.tl-section-subtitle{color:var(--tl-muted);font-size:.9rem;line-height:1.7;margin:0 0 1rem}
.tl-empty{border:1px dashed #c8d8cb;background:#f0f5ef;border-radius:15px;padding:1.4rem 1.6rem;margin:.5rem 0 1rem}
.tl-empty strong{display:block;font-size:1rem;color:var(--tl-ink);margin-bottom:.4rem}
.tl-empty p{color:var(--tl-muted);font-size:.88rem;line-height:1.8;margin:0;word-break:keep-all}
.tl-info-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;margin:1.2rem 0 2rem}
.tl-info-card{background:#fff;border:1px solid var(--tl-line);border-radius:15px;padding:1.35rem}
.tl-info-card .tl-number{display:inline-block;font-size:.75rem;font-weight:750;color:var(--tl-teal);margin-bottom:.8rem}
.tl-info-card h3{font-size:1rem;color:var(--tl-ink);font-weight:700;margin:0 0 .55rem}
.tl-info-card p{font-size:.87rem;line-height:1.8;color:var(--tl-muted);margin:0;word-break:keep-all}
.tl-footer{display:flex;justify-content:space-between;align-items:center;gap:1rem;border-top:1px solid var(--tl-line);padding:1.4rem 0 0;margin-top:2rem;color:var(--tl-muted);font-size:.78rem;line-height:1.7}
.tl-diff{white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid var(--tl-line);background:#fff;padding:1.15rem;border-radius:12px;max-height:430px;overflow:auto;line-height:1.85;font-size:.94rem}
@media(max-width:760px){
[data-testid="stMainBlockContainer"]{padding:3.8rem 1rem 2rem}
.tl-nav{gap:.5rem;flex-wrap:wrap}.tl-nav-links{gap:.85rem;font-size:.76rem}.tl-brand{font-size:1.05rem}
.tl-hero{grid-template-columns:1fr;gap:1.5rem;padding:2rem 0 1.7rem}.tl-hero h1{font-size:2.2rem}
.tl-process{padding:1.2rem 1.35rem;box-shadow:none}.tl-process-step{padding:.6rem 0}
.tl-info-grid{grid-template-columns:1fr;gap:.7rem}.tl-info-card{padding:1.1rem}
.tl-footer{align-items:flex-start;flex-direction:column}.tl-section-title{font-size:1.4rem}
[data-testid="stMetric"]{padding:.8rem}
}
@media(max-width:380px){.tl-nav-links{width:100%;justify-content:space-between}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
""", unsafe_allow_html=True)


def render_hero() -> None:
    st.markdown("""
<nav class="tl-nav" aria-label="주 메뉴">
  <a class="tl-brand" href="#top" aria-label="서스데반의 AI 판독기 처음으로"><span class="tl-mark" aria-hidden="true">✍</span>서스데반의 AI 판독기</a>
  <div class="tl-nav-links"><a href="#workspace">글 다듬기</a><a href="#how-it-works">이용 방법</a><a href="#questions">도움말</a></div>
</nav>
<section id="top" class="tl-hero" aria-labelledby="hero-title">
  <div>
    <p class="tl-eyebrow">WRITE WITH CLARITY</p>
    <h1 id="hero-title">전하고 싶은 의미는 그대로,<br><span>문장은 더 자연스럽게.</span></h1>
    <p class="tl-lead">글의 흐름과 표현을 다듬고, 수정 전후를 직접 비교하세요.<br>원문의 의미를 보존하며 자연스러움과 가독성을 개선합니다.</p>
    <a href="#workspace" class="tl-cta">내 글 다듬기 <span aria-hidden="true">↗</span></a>
    <p class="tl-hero-note">OpenAI 문장 개선을 사용합니다. AI 탐지 점수는 측정하지 않습니다.</p>
  </div>
  <aside class="tl-process" aria-label="문장 개선 과정">
    <p class="tl-process-title">한 번의 실행, 투명한 기록</p>
    <div class="tl-process-step"><b>01</b><div><strong>원문을 기준으로</strong><p>의미와 수치, 인용문을 지키며 개선합니다.</p></div></div>
    <div class="tl-process-step"><b>02</b><div><strong>여러 후보를 비교해</strong><p>의미 보존과 수치·인용 보호 조건을 확인합니다.</p></div></div>
    <div class="tl-process-step"><b>03</b><div><strong>결정은 확인 가능하게</strong><p>중요 수정은 승인하고, 전체 이력을 내려받습니다.</p></div></div>
  </aside>
</section>
""", unsafe_allow_html=True)


def render_empty_state() -> None:
    st.markdown("""
<div class="tl-empty" role="status">
<strong>첫 문장부터 시작해 볼까요?</strong>
<p>위에 글을 붙여 넣거나 PDF·예시 글을 불러온 뒤 <b>시작</b>을 눌러주세요.<br>
실행하면 수정 후보, 의미 검토, 원문 비교가 이곳에 표시됩니다.</p>
</div>
""", unsafe_allow_html=True)


def render_guide(history_enabled: bool = True) -> None:
    st.markdown("""
<section id="how-it-works" aria-labelledby="guide-title">
<p class="tl-section-label">HOW IT WORKS</p>
<h2 class="tl-section-title" id="guide-title">세 단계로 살펴보는 문장 개선</h2>
<div class="tl-info-grid">
<article class="tl-info-card"><span class="tl-number">STEP 01</span><h3>글과 기준을 정하세요</h3><p>원문을 입력하거나 PDF에서 본문을 가져오고 개선 회차와 후보 수를 선택하세요. 그대로 유지할 이름이나 전문 용어도 지정할 수 있습니다.</p></article>
<article class="tl-info-card"><span class="tl-number">STEP 02</span><h3>의미를 지키며 비교하세요</h3><p>서로 다른 수정 후보를 비교합니다. 의미 보존과 보호 조건을 통과한 후보를 검토하며, 중요 수정은 승인을 기다립니다.</p></article>
<article class="tl-info-card"><span class="tl-number">STEP 03</span><h3>과정까지 가져가세요</h3><p>원문과 결과를 나란히 확인하세요. 최종 글과 수정 이력은 PDF·DOCX로, 전체 실행 기록은 JSON으로 저장할 수 있습니다.</p></article>
</div>
</section>
<div id="questions"><p class="tl-section-label">GOOD TO KNOW</p><h2 class="tl-section-title">시작하기 전에 알아두세요</h2></div>
""", unsafe_allow_html=True)
    with st.expander("테스트 모드와 실제 문장 개선은 어떻게 다른가요?"):
        st.write("테스트용 Mock은 공백과 줄바꿈을 조정하며 실행 흐름을 체험하는 모드입니다. 실제 API 모드는 운영자가 설정한 OpenAI로 문장을 개선하고 의미 보존을 검토하며, 입력 글 전송에 동의한 뒤 실행합니다.")
    with st.expander("AI 탐지 점수가 낮아지나요?"):
        st.write("이 사이트는 AI 탐지 점수를 측정하지 않습니다. 문장이 자연스러워져도 탐지 점수가 낮아졌는지 확인할 수 없으며, 점수 하락·탐지 회피·인간 작성 여부를 보장하지 않습니다. 의미 보존 검토도 모델 평가이므로 최종 사실관계는 직접 확인해 주세요.")
    with st.expander("입력한 글과 수정 기록은 어디에 남나요?"):
        if history_enabled:
            st.write("원문과 수정 이력은 이 웹사이트를 실행하는 서버에 평문으로 저장됩니다. 저장 기록 삭제는 사이트 운영자에게 요청할 수 있습니다.")
        else:
            st.write("이 사이트는 서버에 실행 기록 파일을 남기지 않도록 설정되어 있습니다. 글과 결과는 실행 중인 세션에 유지되며, 직접 다운로드할 수 있습니다.")
        st.write("실제 API 모드에서는 원문과 수정 후보가 OpenAI에 전송됩니다. 비밀정보와 민감한 개인정보는 입력하지 마세요.")
    st.markdown("""
<footer class="tl-footer"><span><b>서스데반의 AI 판독기</b></span><span>글의 의미와 사실관계를 직접 확인하세요.</span></footer>
""", unsafe_allow_html=True)

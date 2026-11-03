"""Editing uses meaning/protection checks and approvals without a detector."""
import asyncio
import json

import pytest

from textlab.editor import EditingEngine
from textlab.engine import Control
from textlab.models import Revision, SemanticAssessment, Settings
from textlab.transport import APIError, Budget, BudgetExceeded


ORIGINAL = "글의 의미를 유지하며 문장을 읽기 쉽게 다듬습니다."


class Writer:
    is_mock = False

    def __init__(self, batches, assessments=None):
        self.batches = iter(batches)
        self.assessments = assessments or {}
        self.generation_calls = []
        self.assessment_calls = []

    async def generate(self, original, current, count, analysis):
        self.generation_calls.append((original, current, count, analysis))
        result = next(self.batches)
        if isinstance(result, Exception):
            raise result
        return result

    async def assess(self, original, candidate):
        self.assessment_calls.append((original, candidate))
        result = self.assessments.get(candidate, SemanticAssessment(0.99, True, "원문 의미 유지"))
        if isinstance(result, Exception):
            raise result
        return result

    async def detect(self, text):
        pytest.fail("The editing path must not call any detector")


def revisions(*texts, major=False):
    return [Revision(text, ["흐름과 표현을 다듬음"], major) for text in texts]


def execute(tmp_path, writer, *, original=ORIGINAL, settings=None, **kwargs):
    settings = settings or Settings(max_iterations=1, require_approval=False)
    return asyncio.run(EditingEngine(writer, Budget(settings), tmp_path).run(
        original, settings, **kwargs))


def assert_unmeasured(state):
    assert state.editing_only
    assert state.initial is state.current is state.best is None
    assert all(i.best_score is None for i in state.iterations)
    assert all(c.detection is None for i in state.iterations for c in i.candidates)


def test_first_eligible_candidate_is_adopted_without_detector_or_fabricated_scores(tmp_path):
    writer = Writer([revisions("첫 번째 개선 문장", "다음 개선 문장", "마지막 개선 문장")])

    state = execute(tmp_path, writer)

    assert state.status == "completed"
    assert state.current_text == state.best_text == "첫 번째 개선 문장"
    assert state.iterations[0].adopted_id == "1-1"
    assert writer.assessment_calls == [(ORIGINAL, "첫 번째 개선 문장")]
    assert all(c.reason for c in state.iterations[0].candidates)
    assert_unmeasured(state)
    persisted = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert persisted == state.to_dict()
    assert "점수 개선" not in persisted["iterations"][0]["candidates"][0]["reason"]


def test_semantic_failures_are_rejected_and_each_round_uses_the_original(tmp_path):
    writer = Writer([
        revisions("뜻이 달라진 문장", "일부 내용을 빠뜨린 문장", "첫 채택 문장"),
        revisions("두 번째 채택 문장", "후속 후보", "다른 후속 후보"),
    ], {
        "뜻이 달라진 문장": SemanticAssessment(0.99, False, "주장 반전"),
        "일부 내용을 빠뜨린 문장": SemanticAssessment(0.89, True, "일부 내용 생략"),
    })

    state = execute(tmp_path, writer, settings=Settings(max_iterations=2, require_approval=False))

    assert state.status == "completed"
    assert state.best_text == "두 번째 채택 문장"
    assert all(original == ORIGINAL for original, _ in writer.assessment_calls)
    assert [call[1] for call in writer.generation_calls] == [ORIGINAL, "첫 채택 문장"]
    assert all("의미 보존" in c.reason for c in state.iterations[0].candidates[:2])
    assert_unmeasured(state)


@pytest.mark.parametrize(("original", "changed", "terms"), [
    ("서울의 수치는 10%입니다.", "서울의 수치는 11%입니다.", []),
    ('연구자는 "정확한 인용"을 남겼습니다.', '연구자는 "변경된 인용"을 남겼습니다.', []),
    ("연구 결론입니다 [1].", "연구 결론입니다 [2].", []),
    ("서울 연구원이 발표했습니다.", "부산 연구원이 발표했습니다.", ["서울"]),
    ("출처 https://example.org/report", "출처 https://example.org/other", []),
])
def test_protected_facts_are_rejected_before_semantic_requests(tmp_path, original, changed, terms):
    writer = Writer([revisions(changed, original, changed)])

    state = execute(tmp_path, writer, original=original,
                    settings=Settings(max_iterations=1, require_approval=False, protected_terms=terms))

    assert state.status == "completed"
    assert state.best_text == original
    assert writer.assessment_calls == []
    assert all(c.reason for c in state.iterations[0].candidates)
    assert_unmeasured(state)


def test_no_new_candidate_ends_immediately_without_unnecessary_rounds(tmp_path):
    writer = Writer([revisions(ORIGINAL, ORIGINAL, ORIGINAL)])

    state = execute(tmp_path, writer, settings=Settings(require_approval=False))

    assert state.status == "completed"
    assert len(state.iterations) == 1
    assert state.best_text == ORIGINAL
    assert not writer.assessment_calls
    assert "새로운 후보" in state.stop_reason


def test_major_changes_need_exact_approval_and_rejection_falls_back_in_order(tmp_path):
    control = Control()
    pending = []
    writer = Writer([revisions("선호 후보", "승인할 후보", "예비 후보", major=True)])

    def review(state):
        if state.status == "awaiting_approval":
            candidate = state.pending_candidate
            pending.append((candidate.id, state.current_text))
            control.approvals.put(("unrelated-candidate", True))
            control.approvals.put((candidate.id, candidate.text == "승인할 후보"))

    state = execute(tmp_path, writer, settings=Settings(max_iterations=1),
                    on_update=review, control=control)

    assert state.status == "completed"
    assert pending == [("1-1", ORIGINAL), ("1-2", ORIGINAL)]
    assert state.best_text == "승인할 후보"
    assert state.iterations[0].adopted_id == "1-2"
    assert "거부" in state.iterations[0].candidates[0].reason
    assert writer.assessment_calls == [(ORIGINAL, "선호 후보"), (ORIGINAL, "승인할 후보")]
    assert state.pending_candidate is None
    assert_unmeasured(state)


def test_stop_during_later_approval_preserves_the_last_approved_text(tmp_path):
    control = Control()
    writer = Writer([
        revisions("첫 승인본", "첫 예비 후보", "첫 마지막 후보", major=True),
        revisions("승인하지 않은 수정", "후속 예비 후보", "후속 마지막 후보", major=True),
    ])

    def review(state):
        if state.status == "awaiting_approval":
            if state.pending_candidate.id == "1-1":
                control.approvals.put(("1-1", True))
            else:
                control.stop.set()

    state = execute(tmp_path, writer, settings=Settings(max_iterations=2),
                    on_update=review, control=control)

    assert state.status == "stopped"
    assert state.best_text == state.current_text == "첫 승인본"
    assert state.pending_candidate is None
    assert not any(c.accepted for c in state.iterations[1].candidates)
    assert_unmeasured(state)


def test_stop_cancels_an_in_flight_openai_generation(tmp_path):
    control = Control()

    class SlowWriter(Writer):
        cancelled = False

        async def generate(self, *args):
            try:
                await asyncio.sleep(30)
            finally:
                self.cancelled = True

    async def scenario():
        writer = SlowWriter([])
        settings = Settings()
        task = asyncio.create_task(EditingEngine(writer, Budget(settings), tmp_path).run(
            ORIGINAL, settings, control=control))
        await asyncio.sleep(0.01)
        control.stop.set()
        state = await asyncio.wait_for(task, timeout=1)
        assert writer.cancelled
        return state

    state = asyncio.run(scenario())
    assert state.status == "stopped"
    assert state.best_text == ORIGINAL
    assert_unmeasured(state)


@pytest.mark.parametrize(("failure", "status"), [
    (BudgetExceeded("quota"), "stopped"),
    (APIError("OpenAI 요청이 거부되었습니다."), "error"),
    (RuntimeError("private-response-key"), "error"),
])
def test_budget_and_provider_failures_preserve_the_previous_approved_text(tmp_path, failure, status):
    writer = Writer([revisions("앞서 채택한 문장", "다른 후보", "마지막 후보"), failure])

    state = execute(tmp_path, writer, settings=Settings(max_iterations=2, require_approval=False))

    assert state.status == status
    assert state.best_text == state.current_text == "앞서 채택한 문장"
    assert "private-response-key" not in json.dumps(state.to_dict())
    assert_unmeasured(state)


def test_semantic_api_failure_is_not_treated_as_success_or_adoption(tmp_path):
    writer = Writer([revisions("검토 실패 후보", "다음 후보", "마지막 후보")], {
        "검토 실패 후보": RuntimeError("private-error-body"),
    })

    state = execute(tmp_path, writer)

    assert state.status == "error"
    assert state.best_text == ORIGINAL
    assert not any(c.accepted for c in state.iterations[0].candidates)
    assert "private-error-body" not in json.dumps(state.to_dict())
    assert all(c.reason for c in state.iterations[0].candidates)
    assert_unmeasured(state)


def test_wrong_candidate_count_fails_without_adopting_unchecked_text(tmp_path):
    state = execute(tmp_path, Writer([revisions("하나뿐인 후보")]))

    assert state.status == "error"
    assert "개수" in state.error
    assert state.best_text == ORIGINAL
    assert_unmeasured(state)

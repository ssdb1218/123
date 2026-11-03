"""Behavioral tests for selection, rollback, approval, cancellation and auditing."""
import asyncio
import json

import pytest

from textlab.engine import Control, Engine
from textlab.models import Detection, Revision, SemanticAssessment, Settings
from textlab.transport import Budget, BudgetExceeded


ORIGINAL = "원문의 의미를 유지하면서 문장을 읽기 쉽게 다듬습니다."


class Detector:
    is_mock = True

    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    async def detect(self, text):
        self.calls.append(text)
        value = self.scores[text]
        if isinstance(value, Exception):
            raise value
        return Detection(value, "test-detector", True, "테스트 점수")


class Rewriter:
    def __init__(self, batches, assessments=None):
        self.batches = iter(batches)
        self.assessments = assessments or {}
        self.generation_calls = []
        self.assessment_calls = []

    async def generate(self, original, current, count, analysis):
        self.generation_calls.append((original, current, count, analysis))
        return next(self.batches)

    async def assess(self, original, candidate):
        self.assessment_calls.append((original, candidate))
        result = self.assessments.get(candidate, SemanticAssessment(0.99, True, "의미 유지"))
        if isinstance(result, Exception):
            raise result
        return result


def revisions(*texts, major=False):
    return [Revision(text, ["문장 흐름 개선"], major) for text in texts]


def execute(tmp_path, detector, rewriter, *, original=ORIGINAL, settings=None, **kwargs):
    settings = settings or Settings(max_iterations=1, require_approval=False)
    return asyncio.run(Engine(detector, rewriter, Budget(settings), tmp_path).run(
        original, settings, **kwargs))


def test_default_settings_match_requested_workflow():
    settings = Settings()
    settings.validate()
    assert (settings.max_iterations, settings.candidates_per_iteration, settings.target_score) == (20, 3, 5)
    assert settings.patience == 3
    assert settings.require_approval is True


def test_lowest_valid_candidate_wins_and_every_semantic_check_uses_original(tmp_path):
    detector = Detector({ORIGINAL: 80, "첫 후보": 60, "차선 후보": 70, "실패 후보": 90,
                         "개선 후보": 30, "다른 후보": 40, "나쁜 후보": 75})
    writer = Rewriter([revisions("첫 후보", "차선 후보", "실패 후보"),
                       revisions("개선 후보", "다른 후보", "나쁜 후보")])
    settings = Settings(max_iterations=2, target_score=0, require_approval=False)
    state = execute(tmp_path, detector, writer, settings=settings)
    assert state.status == "completed"
    assert state.best_text == state.current_text == "개선 후보"
    assert state.best.score == state.current.score == 30
    assert state.initial.score == 80
    accepted = [c for i in state.iterations for c in i.candidates if c.accepted]
    assert [c.detection.score for c in accepted] == [60, 30]
    assert all(original == ORIGINAL for original, _ in writer.assessment_calls)
    assert [call[1] for call in writer.generation_calls] == [ORIGINAL, "첫 후보"]
    assert all(call[2] == 3 for call in writer.generation_calls)
    assert "최대 반복" in state.stop_reason


def test_score_regression_and_semantic_drift_restore_previous_version(tmp_path):
    detector = Detector({ORIGINAL: 50, "점수가 높아진 문장": 60})
    writer = Rewriter([revisions("점수가 높아진 문장", "뜻이 달라진 문장", "의미 점수 부족")], {
        "뜻이 달라진 문장": SemanticAssessment(0.99, False, "주장 반전"),
        "의미 점수 부족": SemanticAssessment(0.89, True, "일부 의미 생략"),
    })
    state = execute(tmp_path, detector, writer)
    assert state.best_text == state.current_text == ORIGINAL
    assert state.best.score == 50
    assert not any(c.accepted for c in state.iterations[0].candidates)
    assert detector.calls == [ORIGINAL, "점수가 높아진 문장"]
    assert "개선되지" in state.iterations[0].candidates[0].reason
    assert all("의미 보존" in c.reason for c in state.iterations[0].candidates[1:])


@pytest.mark.parametrize(("original", "changed", "terms"), [
    ("서울의 수치는 10%입니다.", "서울의 수치는 11%입니다.", []),
    ("측정값은 -10입니다.", "측정값은 +10입니다.", []),
    ("비율은 .5입니다.", "비율은 5입니다.", []),
    ('연구자는 "정확한 인용"을 남겼습니다.', '연구자는 "바뀐 인용"을 남겼습니다.', []),
    ("연구의 결론입니다 [1].", "연구의 결론입니다 [2].", []),
    ("서울 연구원이 발표했습니다.", "부산 연구원이 발표했습니다.", ["서울"]),
    ("연구를 소개합니다.\n참고문헌\n김연구. 연구 보고서.",
     "연구를 소개합니다.\n참고문헌\n박연구. 연구 보고서.", []),
    ("NASA의 보고서를 읽었습니다.", "ESA의 보고서를 읽었습니다.", []),
    ("출처 https://example.org/report", "출처 https://example.org/other", []),
])
def test_protected_fact_changes_are_rejected_before_api_assessment(tmp_path, original, changed, terms):
    writer = Rewriter([revisions(changed, original, changed)])
    detector = Detector({original: 70})
    state = execute(tmp_path, detector, writer, original=original,
                    settings=Settings(max_iterations=1, require_approval=False, protected_terms=terms))
    assert state.status == "completed"
    assert state.best_text == original
    assert writer.assessment_calls == []
    assert detector.calls == [original]
    assert all(c.reason for c in state.iterations[0].candidates)


def test_three_rounds_without_improvement_stop_early(tmp_path):
    writer = Rewriter([revisions(ORIGINAL, ORIGINAL, ORIGINAL) for _ in range(3)])
    state = execute(tmp_path, Detector({ORIGINAL: 70}), writer,
                    settings=Settings(require_approval=False))
    assert state.status == "completed"
    assert len(state.iterations) == 3
    assert "3회 연속" in state.stop_reason


def test_target_reached_stops_after_first_adoption(tmp_path):
    detector = Detector({ORIGINAL: 80, "목표 후보": 4, "중간 후보": 20, "높은 후보": 30})
    writer = Rewriter([revisions("목표 후보", "중간 후보", "높은 후보")])
    state = execute(tmp_path, detector, writer, settings=Settings(require_approval=False))
    assert state.best.score == 4
    assert len(state.iterations) == 1
    assert "목표 점수" in state.stop_reason


def test_initial_target_needs_no_generation(tmp_path):
    writer = Rewriter([])
    state = execute(tmp_path, Detector({ORIGINAL: 2}), writer)
    assert state.status == "completed"
    assert not writer.generation_calls
    assert not state.iterations


def test_major_revision_requires_exact_candidate_approval_and_can_fall_back(tmp_path):
    control = Control()
    writer = Rewriter([revisions("가장 낮은 후보", "다음 후보", "마지막 후보", major=True)])
    detector = Detector({ORIGINAL: 80, "가장 낮은 후보": 10, "다음 후보": 20, "마지막 후보": 30})
    pending = []

    def review(state):
        if state.status == "awaiting_approval":
            candidate = state.pending_candidate
            pending.append((candidate.id, state.current_text))
            control.approvals.put(("stale-candidate", True))
            control.approvals.put((candidate.id, candidate.text == "다음 후보"))

    state = execute(tmp_path, detector, writer, settings=Settings(max_iterations=1),
                    control=control, on_update=review)
    assert pending == [("1-1", ORIGINAL), ("1-2", ORIGINAL)]
    assert state.best_text == "다음 후보"
    assert "거부" in state.iterations[0].candidates[0].reason
    assert state.iterations[0].adopted_id == "1-2"
    assert state.pending_candidate is None


def test_stop_while_waiting_for_approval_preserves_original(tmp_path):
    control = Control()
    writer = Rewriter([revisions("수정 후보", "다른 후보", "마지막 후보", major=True)])
    detector = Detector({ORIGINAL: 80, "수정 후보": 10, "다른 후보": 20, "마지막 후보": 30})

    def review(state):
        if state.status == "awaiting_approval":
            control.stop.set()

    state = execute(tmp_path, detector, writer, settings=Settings(max_iterations=1),
                    control=control, on_update=review)
    assert state.status == "stopped"
    assert state.best_text == ORIGINAL
    assert state.pending_candidate is None
    assert not any(c.accepted for c in state.iterations[0].candidates)


def test_stop_cancels_in_flight_detection(tmp_path):
    control = Control()

    class SlowDetector:
        is_mock = True
        cancelled = False

        async def detect(self, text):
            try:
                await asyncio.sleep(30)
            finally:
                self.cancelled = True

    async def scenario():
        detector = SlowDetector()
        settings = Settings()
        task = asyncio.create_task(Engine(detector, Rewriter([]), Budget(settings), tmp_path).run(
            ORIGINAL, settings, control=control))
        await asyncio.sleep(0.01)
        control.stop.set()
        state = await asyncio.wait_for(task, timeout=1)
        assert detector.cancelled
        return state

    state = asyncio.run(scenario())
    assert state.status == "stopped"
    assert state.initial is state.current is state.best is None
    assert state.best_text == ORIGINAL


def test_quota_exhaustion_preserves_prior_best_and_persists_complete_audit(tmp_path):
    writer = Rewriter([revisions("채택 후보", "높은 후보", "더 높은 후보"),
                       revisions("한도 후보", "한도 후보 둘", "한도 후보 셋")])
    detector = Detector({ORIGINAL: 80, "채택 후보": 40, "높은 후보": 60, "더 높은 후보": 70,
                         **{text: BudgetExceeded("quota") for text in ["한도 후보", "한도 후보 둘", "한도 후보 셋"]}})
    state = execute(tmp_path, detector, writer,
                    settings=Settings(max_iterations=2, require_approval=False))
    assert state.status == "stopped"
    assert state.best_text == "채택 후보"
    assert state.best.score == 40
    assert "한도" in state.stop_reason
    records = list(tmp_path.glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record == state.to_dict()
    assert record["iterations"][0]["adopted_id"] == "1-1"
    assert len(record["iterations"][1]["candidates"]) == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_initial_api_failure_has_no_fabricated_score_and_sanitizes_error(tmp_path):
    state = execute(tmp_path, Detector({ORIGINAL: RuntimeError("secret-token-response")}), Rewriter([]))
    assert state.status == "error"
    assert state.initial is state.best is state.current is None
    assert state.best_text == ORIGINAL
    assert "secret-token-response" not in json.dumps(state.to_dict())


def test_failed_candidate_evaluations_are_errors_not_success_or_improvement(tmp_path):
    writer = Rewriter([revisions("실패 문장", "다른 실패", "마지막 실패")],
                      {text: RuntimeError("private-response") for text in ["실패 문장", "다른 실패", "마지막 실패"]})
    state = execute(tmp_path, Detector({ORIGINAL: 80}), writer)
    assert state.status == "error"
    assert state.best.score == 80
    assert state.best_text == ORIGINAL
    assert all(c.detection is None for c in state.iterations[0].candidates)
    assert all("실패" in c.reason for c in state.iterations[0].candidates)
    assert "private-response" not in json.dumps(state.to_dict())


def test_wrong_candidate_count_fails_explicitly(tmp_path):
    state = execute(tmp_path, Detector({ORIGINAL: 80}), Rewriter([revisions("하나뿐인 후보")]))
    assert state.status == "error"
    assert "개수" in state.error
    assert state.best_text == ORIGINAL

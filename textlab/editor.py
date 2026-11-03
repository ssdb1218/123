"""Meaning-preserving editing without detector calls or inferred detection scores."""
from __future__ import annotations

import asyncio
from pathlib import Path
from queue import Empty
from typing import Callable

from .engine import Control, Engine, Stopped
from .guards import analyze, change_ratio, preservation_errors
from .models import Candidate, Iteration, RunState, Settings
from .transport import APIError, BudgetExceeded


class EditingEngine(Engine):
    """Review candidates in the writer's preferred order and retain approved edits."""

    def __init__(self, rewriter, budget, history_dir: Path | str | None = ".runs"):
        # Only cancellation support is inherited; editing has no detector.
        self.rewriter, self.budget = rewriter, budget
        self.history_dir = Path(history_dir) if history_dir is not None else None

    async def run(self, text: str, settings: Settings, on_update: Callable | None = None,
                  control: Control | None = None) -> RunState:
        from .storage import save_state

        control = control or Control()
        state = RunState(original_text=text, settings=settings, current_text=text, best_text=text,
                         is_mock=bool(getattr(self.rewriter, "is_mock", False)), editing_only=True)

        def emit():
            state.usage = self.budget.snapshot()
            if self.history_dir is not None:
                save_state(state, self.history_dir)
            if on_update:
                on_update(state)

        try:
            settings.validate()
            if not text.strip():
                raise ValueError("원본 텍스트를 입력하세요.")
            if len(text) > 20_000:
                raise ValueError("입력은 최대 20,000자입니다.")
            if any(not term or term not in text for term in settings.protected_terms):
                raise ValueError("보호 용어는 원문에 있는 비어 있지 않은 문자열이어야 합니다.")
            state.analysis = analyze(text)
            state.status = "running"
            emit()

            for number in range(1, settings.max_iterations + 1):
                if control.stop.is_set():
                    raise Stopped()
                iteration = Iteration(number=number)
                state.iterations.append(iteration)
                emit()
                revisions = await self._call(self.rewriter.generate(
                    text, state.current_text, settings.candidates_per_iteration,
                    {**analyze(state.current_text), "protected_terms": settings.protected_terms}), control)
                if len(revisions) != settings.candidates_per_iteration:
                    raise ValueError("모델이 요청한 개수의 후보를 반환하지 않았습니다.")

                seen = {state.current_text}
                for index, revision in enumerate(revisions, 1):
                    candidate = Candidate(
                        f"{number}-{index}", revision.text, revision.changes,
                        revision.major_change or change_ratio(state.current_text, revision.text)
                        >= settings.major_change_threshold,
                    )
                    iteration.candidates.append(candidate)
                    if not revision.text.strip() or len(revision.text) > 20_000:
                        candidate.reason = "비어 있거나 길이 제한을 초과한 후보"
                    elif revision.text in seen:
                        candidate.reason = "동일하거나 중복된 후보"
                    else:
                        seen.add(revision.text)
                        candidate.reason = "; ".join(preservation_errors(text, revision.text, settings.protected_terms))
                emit()

                adopted = None
                for candidate in iteration.candidates:
                    if candidate.reason:
                        continue
                    try:
                        # Always compare to the first input, preventing cumulative drift.
                        candidate.semantic = await self._call(
                            self.rewriter.assess(text, candidate.text), control)
                    except (Stopped, BudgetExceeded):
                        raise
                    except Exception:
                        candidate.reason = "후보의 의미 검토 요청이 실패함"
                        raise
                    if (not candidate.semantic.preserves_meaning
                            or candidate.semantic.score < settings.min_semantic_score):
                        candidate.reason = "원문 의미 보존 기준 미달: " + candidate.semantic.reason
                        emit()
                        continue
                    if control.stop.is_set():
                        raise Stopped()
                    if settings.require_approval and candidate.major_change:
                        state.status, state.pending_candidate = "awaiting_approval", candidate
                        emit()
                        approved = None
                        while approved is None:
                            if control.stop.is_set():
                                raise Stopped()
                            try:
                                candidate_id, decision = control.approvals.get_nowait()
                                if candidate_id == candidate.id and isinstance(decision, bool):
                                    approved = decision
                            except Empty:
                                await asyncio.sleep(0.1)
                        state.pending_candidate = None
                        state.status = "running"
                        if not approved:
                            candidate.reason = "사용자가 중요 수정 채택을 거부함"
                            emit()
                            continue
                    candidate.accepted = True
                    candidate.reason = "보호 조건·의미 보존 검토 및 필요한 사용자 승인 완료"
                    state.current_text = state.best_text = candidate.text
                    iteration.adopted_id = candidate.id
                    adopted = candidate
                    break

                for candidate in iteration.candidates:
                    if not candidate.reason:
                        candidate.reason = "앞선 유효 후보 채택으로 검토하지 않음"
                emit()
                if adopted is None:
                    state.stop_reason = "채택할 수 있는 새로운 후보가 없어 종료했습니다. 이전 채택본을 유지합니다."
                    break
            state.stop_reason = state.stop_reason or "설정한 문장 개선 횟수를 완료했습니다. AI 탐지 점수는 측정하지 않았습니다."
            state.status = "completed"
        except Stopped:
            state.status, state.stop_reason = "stopped", "사용자 요청으로 중지했습니다. 마지막 채택본을 유지합니다."
        except BudgetExceeded:
            state.status, state.stop_reason = "stopped", "API 호출 또는 예약 비용 한도에 도달했습니다. 마지막 채택본을 유지합니다."
        except Exception as exc:
            state.status = "error"
            state.error = str(exc) if isinstance(exc, (ValueError, APIError)) else (
                f"실행 실패 ({type(exc).__name__}). OpenAI 설정·서비스 상태를 확인하세요.")
            state.stop_reason = "오류로 중단했습니다. 마지막 채택본을 유지합니다."
        finally:
            for iteration in state.iterations:
                for candidate in iteration.candidates:
                    if not candidate.reason:
                        candidate.reason = "실행 중단으로 검토 또는 채택이 완료되지 않음"
            state.pending_candidate = None
            emit()
        return state

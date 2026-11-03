from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from typing import Callable

from .guards import analyze, change_ratio, preservation_errors
from .models import Candidate, Iteration, RunState, Settings


@dataclass
class Control:
    stop: Event = field(default_factory=Event)
    approvals: Queue = field(default_factory=Queue)


class Stopped(Exception):
    pass


class Engine:
    def __init__(self, detector, rewriter, budget, history_dir: Path | str | None = ".runs"):
        self.detector, self.rewriter, self.budget = detector, rewriter, budget
        self.history_dir = Path(history_dir) if history_dir is not None else None

    async def _call(self, awaitable, control: Control):
        """Cancel in-flight asynchronous requests on stop, retaining the best revision."""
        task = asyncio.ensure_future(awaitable)
        try:
            while not task.done():
                if control.stop.is_set():
                    raise Stopped()
                await asyncio.wait({task}, timeout=0.1)
            if control.stop.is_set():
                raise Stopped()
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def run(self, text: str, settings: Settings, on_update: Callable | None = None,
                  control: Control | None = None) -> RunState:
        from .storage import save_state
        from .transport import APIError, BudgetExceeded

        control = control or Control()
        state = RunState(original_text=text, settings=settings, current_text=text, best_text=text,
                         is_mock=bool(getattr(self.detector, "is_mock", False)))

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
            if len(text) > 20000:
                raise ValueError("입력은 최대 20,000자입니다.")
            if any(not term or term not in text for term in settings.protected_terms):
                raise ValueError("보호 용어는 원문에 있는 비어 있지 않은 문자열이어야 합니다.")
            state.analysis = analyze(text)
            emit()
            state.initial = await self._call(self.detector.detect(text), control)
            state.current = state.best = state.initial
            state.status = "running"
            emit()
            if state.best.score <= settings.target_score:
                state.stop_reason = "최초 점수가 목표 이하입니다."
            no_improvement = 0
            for number in range(1, settings.max_iterations + 1):
                if state.stop_reason:
                    break
                if control.stop.is_set():
                    raise Stopped()
                iteration = Iteration(number=number, best_score=state.best.score)
                state.iterations.append(iteration)
                emit()
                revisions = await self._call(self.rewriter.generate(
                    text, state.current_text, settings.candidates_per_iteration,
                    {**analyze(state.current_text), "protected_terms": settings.protected_terms}), control)
                if len(revisions) != settings.candidates_per_iteration:
                    raise ValueError("모델이 요청한 개수의 후보를 반환하지 않았습니다.")
                seen = {state.current_text}
                for index, revision in enumerate(revisions, 1):
                    candidate = Candidate(f"{number}-{index}", revision.text, revision.changes,
                                          revision.major_change or change_ratio(state.current_text, revision.text)
                                          >= settings.major_change_threshold)
                    iteration.candidates.append(candidate)
                    if not revision.text.strip() or len(revision.text) > 20000:
                        candidate.reason = "비어 있거나 길이 제한을 초과한 후보"
                    elif revision.text in seen:
                        candidate.reason = "동일하거나 중복된 후보"
                    else:
                        seen.add(revision.text)
                        candidate.reason = "; ".join(preservation_errors(text, revision.text, settings.protected_terms))
                emit()

                async def evaluate(candidate):
                    if candidate.reason:
                        return
                    try:
                        # Semantic assessment is always anchored to the ORIGINAL to avoid gradual drift.
                        candidate.semantic = await self.rewriter.assess(text, candidate.text)
                        if not candidate.semantic.preserves_meaning or candidate.semantic.score < settings.min_semantic_score:
                            candidate.reason = "원문 의미 보존 기준 미달: " + candidate.semantic.reason
                            return
                        candidate.detection = await self.detector.detect(candidate.text)
                        if candidate.detection.provider != state.initial.provider or candidate.detection.is_mock != state.initial.is_mock:
                            raise ValueError("실행 중 판독 서비스 또는 Mock 여부가 변경되었습니다.")
                        if candidate.detection.score >= state.best.score:
                            candidate.reason = "탐지 점수가 개선되지 않아 이전 버전 유지"
                    except BudgetExceeded:
                        raise
                    except Exception as exc:
                        # Providers expose sanitized errors only; never persist request bodies or credentials.
                        candidate.reason = f"후보 평가 실패 ({type(exc).__name__})"
                        return exc

                tasks = [asyncio.create_task(evaluate(candidate)) for candidate in iteration.candidates]
                try:
                    results = await self._call(asyncio.gather(*tasks, return_exceptions=True), control)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                emit()
                if any(isinstance(result, BudgetExceeded) for result in results):
                    raise BudgetExceeded("API 사용 한도에 도달했습니다.")
                if any(isinstance(result, BaseException) for result in results):
                    raise RuntimeError("일부 후보의 API 평가가 실패했습니다. 설정·서비스 상태를 확인한 뒤 다시 실행하세요.")
                eligible = sorted((c for c in iteration.candidates if not c.reason and c.detection is not None),
                                  key=lambda c: c.detection.score)
                adopted = None
                for candidate in eligible:
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
                    candidate.reason = "점수 개선·의미 보존 통과 및 필요한 사용자 승인 완료"
                    state.current_text = state.best_text = candidate.text
                    state.current = state.best = candidate.detection
                    iteration.adopted_id, iteration.best_score = candidate.id, candidate.detection.score
                    adopted = candidate
                    break
                for candidate in eligible:
                    if not candidate.reason:
                        candidate.reason = "더 낮은 점수의 유효 후보 채택으로 보류"
                no_improvement = 0 if adopted else no_improvement + 1
                emit()
                if state.best.score <= settings.target_score:
                    state.stop_reason = "목표 점수에 도달했습니다. 인간 작성 여부를 보장하지 않습니다."
                elif no_improvement >= settings.patience:
                    state.stop_reason = f"{settings.patience}회 연속 개선이 없어 조기 종료했습니다."
            state.stop_reason = state.stop_reason or "최대 반복 횟수에 도달해 가장 좋은 결과를 반환했습니다."
            state.status = "completed"
        except Stopped:
            state.status, state.stop_reason = "stopped", "사용자 요청으로 중지했습니다. 최저 점수 버전을 유지합니다."
        except BudgetExceeded:
            state.status, state.stop_reason = "stopped", "API 호출 또는 예약 비용 한도에 도달했습니다. 최저 점수 버전을 유지합니다."
        except Exception as exc:
            state.status = "error"
            # Validation errors are safe text; arbitrary backend errors may contain sensitive data.
            state.error = str(exc) if isinstance(exc, (ValueError, APIError)) else f"실행 실패 ({type(exc).__name__}). API 설정·서비스 상태를 확인하세요."
            state.stop_reason = "오류로 중단했습니다. 이전 최선 버전을 보존합니다."
        finally:
            for iteration in state.iterations:
                for candidate in iteration.candidates:
                    if not candidate.reason:
                        candidate.reason = "실행 중단으로 평가 또는 채택이 완료되지 않음"
            state.pending_candidate = None
            emit()
        return state

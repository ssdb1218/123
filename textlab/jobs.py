"""Session-local background execution; workers never call Streamlit APIs."""
from __future__ import annotations

import asyncio
import copy
import threading
from typing import Any

from textlab.models import RunState, Settings


_RUN_LOCK = threading.Lock()
_ACTIVE_RUNS = 0


class Worker:
    def __init__(self, text: str, settings: Settings, mode: str) -> None:
        from textlab.engine import Control

        self.control = Control()
        self.mode = mode
        self._lock = threading.Lock()
        self._state = RunState(original_text=text, settings=copy.deepcopy(settings), is_mock=mode == "mock")
        self._answered: set[str] = set()
        self._started = False
        self._slot_reserved = False
        self._thread = threading.Thread(target=self._run, name=f"textlab-{self._state.id[:8]}", daemon=True)

    def start(self) -> None:
        from textlab.web_runtime import max_concurrent_runs

        global _ACTIVE_RUNS
        with _RUN_LOCK:
            if self._started:
                raise ValueError("이미 시작한 작업입니다. 새 실행을 만들어 주세요.")
            if _ACTIVE_RUNS >= max_concurrent_runs():
                raise ValueError("현재 다른 글을 처리하고 있습니다. 잠시 후 다시 시작해 주세요.")
            _ACTIVE_RUNS += 1
            self._slot_reserved = True
            self._started = True
        try:
            self._thread.start()
        except BaseException:
            self._release_slot()
            raise

    def _release_slot(self) -> None:
        global _ACTIVE_RUNS
        with _RUN_LOCK:
            if self._slot_reserved:
                _ACTIVE_RUNS -= 1
                self._slot_reserved = False

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def snapshot(self) -> RunState:
        with self._lock:
            return copy.deepcopy(self._state)

    def _publish(self, state: RunState) -> None:
        with self._lock:
            self._state = copy.deepcopy(state)

    def stop(self) -> None:
        self.control.stop.set()

    def respond(self, candidate_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._state.pending_candidate
            if not pending or pending.id != candidate_id or candidate_id in self._answered:
                return False
            self._answered.add(candidate_id)
        self.control.approvals.put((candidate_id, approved))
        return True

    def has_responded(self, candidate_id: str) -> bool:
        with self._lock:
            return candidate_id in self._answered

    def _run(self) -> None:
        try:
            asyncio.run(self._execute())
        finally:
            self._release_slot()

    async def _execute(self) -> None:
        from textlab.engine import Engine
        from textlab.providers import build_providers
        from textlab.web_runtime import audit_directory, validate_live_hosting

        client: Any = None
        try:
            state = self.snapshot()
            if self.mode == "live":
                validate_live_hosting()
            detector, rewriter, budget, client = build_providers(self.mode, state.settings)
            result = await Engine(detector, rewriter, budget, history_dir=audit_directory()).run(
                state.original_text, state.settings, on_update=self._publish, control=self.control
            )
            self._publish(result)
        except Exception as exc:
            # Provider/configuration exceptions have deliberately sanitized messages.
            # Never expose arbitrary transport exceptions, HTTP bodies, or secrets.
            state = self.snapshot()
            state.status = "error"
            state.error = str(exc) if isinstance(exc, ValueError) else "실행을 완료하지 못했습니다. API 설정과 네트워크 연결을 확인해 주세요."
            state.stop_reason = "설정 또는 실행 오류"
            self._publish(state)
        finally:
            if client is not None:
                await client.aclose()

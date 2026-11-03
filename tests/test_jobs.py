"""Hosted jobs respect process capacity and server-side persistence policy."""
import asyncio
import threading

import pytest

from textlab.jobs import Worker
from textlab.models import Settings


def test_capacity_is_shared_between_sessions_and_released_on_exit(monkeypatch):
    monkeypatch.setenv("TEXTLAB_MAX_CONCURRENT_RUNS", "1")
    entered = threading.Event()
    release = threading.Event()

    async def wait(self):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(Worker, "_execute", wait)
    first, second = [Worker("테스트 글", Settings(), "mock") for _ in range(2)]
    first.start()
    try:
        assert entered.wait(timeout=2)
        with pytest.raises(ValueError, match="잠시 후"):
            second.start()
        with pytest.raises(ValueError, match="이미 시작"):
            first.start()
    finally:
        release.set()
        first._thread.join(timeout=2)
    assert not first.is_alive
    second.start()
    second._thread.join(timeout=2)
    assert not second.is_alive


def test_start_failure_releases_capacity(monkeypatch):
    monkeypatch.setenv("TEXTLAB_MAX_CONCURRENT_RUNS", "1")
    failed = Worker("테스트 글", Settings(), "mock")

    def fail():
        raise RuntimeError("cannot create thread")

    monkeypatch.setattr(failed._thread, "start", fail)
    with pytest.raises(RuntimeError):
        failed.start()

    async def finish(self):
        pass

    monkeypatch.setattr(Worker, "_execute", finish)
    following = Worker("다음 테스트", Settings(), "mock")
    following.start()
    following._thread.join(timeout=2)
    assert not following.is_alive


def test_hosted_demo_does_not_persist_plaintext_history(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEXTLAB_HOSTED", "1")
    monkeypatch.setenv("TEXTLAB_SAVE_HISTORY", "0")
    worker = Worker("책을 읽고 생각을 기록합니다.", Settings(require_approval=False), "mock")
    worker.start()
    worker._thread.join(timeout=5)
    state = worker.snapshot()
    assert not worker.is_alive
    assert state.status == "completed"
    assert state.is_mock
    assert state.editing_only
    assert state.initial is state.current is state.best is None
    assert not (tmp_path / ".runs").exists()
    assert state.to_dict()["original_text"] == "책을 읽고 생각을 기록합니다."


def test_hosted_live_without_access_password_fails_before_provider_call(monkeypatch):
    import textlab.providers as providers

    monkeypatch.setenv("TEXTLAB_HOSTED", "1")
    monkeypatch.delenv("APP_ACCESS_PASSWORD", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Live provider must not be constructed before hosted access validation")

    monkeypatch.setattr(providers, "build_editing_providers", forbidden)
    worker = Worker("실제 API 테스트", Settings(), "live")
    worker.start()
    worker._thread.join(timeout=2)
    state = worker.snapshot()
    assert state.status == "error"
    assert "APP_ACCESS_PASSWORD" in state.error
    assert state.initial is None


def test_live_worker_uses_only_editor_providers_and_closes_client(monkeypatch, tmp_path):
    import textlab.providers as providers
    from textlab.models import Revision, SemanticAssessment
    from textlab.transport import Budget

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEXTLAB_HOSTED", "0")
    monkeypatch.delenv("GPTZERO_API_KEY", raising=False)
    constructed = []

    class Writer:
        is_mock = False

        async def generate(self, original, current, count, analysis):
            return [Revision("표현을 자연스럽게 다듬은 문장입니다.", ["문장 흐름 개선"])]

        async def assess(self, original, candidate):
            return SemanticAssessment(0.99, True, "의미 유지")

    class Client:
        closed = False

        async def aclose(self):
            self.closed = True

    client = Client()

    def build_editing(mode, settings):
        constructed.append(mode)
        return Writer(), Budget(settings), client

    def forbidden(*args, **kwargs):
        pytest.fail("The website worker must not construct a detector")

    monkeypatch.setattr(providers, "build_editing_providers", build_editing)
    monkeypatch.setattr(providers, "build_providers", forbidden)
    worker = Worker("표현을 개선하기 위한 원문입니다.",
                    Settings(max_iterations=1, candidates_per_iteration=1, require_approval=False), "live")
    assert worker.snapshot().editing_only
    worker.start()
    worker._thread.join(timeout=3)
    state = worker.snapshot()
    assert not worker.is_alive
    assert state.status == "completed"
    assert constructed == ["live"]
    assert client.closed
    assert state.editing_only and not state.is_mock
    assert state.initial is state.current is state.best is None
    assert state.best_text == "표현을 자연스럽게 다듬은 문장입니다."

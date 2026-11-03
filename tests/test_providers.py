import asyncio
import json

import httpx
import pytest

from textlab.models import Settings
from textlab.providers import (
    GPTZeroDetector, MockDetector, MockRewriter, OpenAIRewriter, build_editing_providers, build_providers,
)
from textlab.transport import APIError, AsyncTransport, Budget, BudgetExceeded, ConfigurationError


def run(coro):
    return asyncio.run(coro)


def llm_response(content, *, finish_reason="stop"):
    return {"choices": [{"finish_reason": finish_reason,
                         "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def test_gptzero_official_contract_and_score():
    async def scenario():
        def handler(request):
            assert str(request.url) == "https://api.gptzero.me/v2/predict/text"
            assert request.headers["x-api-key"] == "test-detector-key"
            assert json.loads(request.content) == {"document": "원문입니다."}
            return httpx.Response(200, json={"documents": [{"completely_generated_prob": 0.43}]})
        budget = Budget(Settings())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            detector = GPTZeroDetector(AsyncTransport(client, budget), "test-detector-key")
            value = await detector.detect("원문입니다.")
        assert value.score == 43
        assert value.provider == "GPTZero" and not value.is_mock
        assert budget.snapshot()["api_calls"] == 1
        assert budget.snapshot()["reserved_cost_usd"] == 0.01
    run(scenario())


@pytest.mark.parametrize("score", [None, "0.3", -0.1, 1.1, True, float("nan"), float("inf")])
def test_detector_invalid_scores_fail_closed(score):
    async def scenario():
        # json.dumps normally allows NaN but httpx's JSON helper rejects it.
        data = json.dumps({"documents": [{"completely_generated_prob": score}]}).encode()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=data)
        )) as client:
            detector = GPTZeroDetector(AsyncTransport(client, Budget(Settings())), "key")
            with pytest.raises(APIError):
                await detector.detect("sample")
    run(scenario())


def test_missing_response_field_never_falls_back_to_mock():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"documents": [{}]})
        )) as client:
            with pytest.raises(APIError, match="점수가 없습니다"):
                await GPTZeroDetector(AsyncTransport(client, Budget(Settings())), "key").detect("sample")
    run(scenario())


def test_retry_counts_every_attempt_and_then_succeeds():
    async def scenario():
        attempts = []
        def handler(request):
            attempts.append(request)
            if len(attempts) < 3:
                return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "private body"})
            return httpx.Response(200, json={"documents": [{"completely_generated_prob": .1}]})
        budget = Budget(Settings(max_api_calls=3))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GPTZeroDetector(AsyncTransport(client, budget, backoff_seconds=0), "key").detect("x")
        assert result.score == 10
        assert budget.snapshot()["api_calls"] == 3
        assert budget.snapshot()["reserved_cost_usd"] == .03
    run(scenario())


def test_retry_is_stopped_before_exceeding_call_budget():
    async def scenario():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={})
        budget = Budget(Settings(max_api_calls=1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BudgetExceeded):
                await GPTZeroDetector(AsyncTransport(client, budget, backoff_seconds=0), "key").detect("x")
        assert len(calls) == 1
        assert budget.snapshot()["api_calls"] == 1
    run(scenario())


def test_cost_budget_reserves_before_sending():
    budget = Budget(Settings(max_cost_usd=.015, detector_request_cost=.01))
    budget.reserve("detector")
    with pytest.raises(BudgetExceeded):
        budget.reserve("detector")
    assert budget.snapshot()["reserved_cost_usd"] == .01
    assert budget.snapshot()["api_calls"] == 1


def test_auth_failure_is_not_retried_and_is_sanitized():
    async def scenario():
        budget = Budget(Settings())
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "secret-key-and-private-text"})
        )) as client:
            with pytest.raises(APIError) as error:
                await GPTZeroDetector(AsyncTransport(client, budget), "secret-key").detect("private-text")
        assert "secret-key" not in str(error.value)
        assert "private-text" not in str(error.value)
        assert budget.snapshot()["api_calls"] == 1
    run(scenario())


def test_openai_json_contract_generate_and_independent_assessment():
    async def scenario():
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            assert request.headers["authorization"] == "Bearer test-llm-key"
            assert str(request.url) == "https://api.openai.com/v1/chat/completions"
            assert requests[-1]["response_format"] == {"type": "json_object"}
            if len(requests) == 1:
                return httpx.Response(200, json=llm_response({"candidates": [
                    {"text": "후보", "changes": ["흐름 개선"], "major_change": True}]}))
            return httpx.Response(200, json=llm_response({
                "score": .97, "preserves_meaning": True, "reason": "동일 사실"}))
        budget = Budget(Settings())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rewriter = OpenAIRewriter(AsyncTransport(client, budget), "test-llm-key")
            revision = (await rewriter.generate("원문", "중간본", 1, {}))[0]
            assert revision.text == "후보" and revision.major_change
            assessment = await rewriter.assess("원문", "후보")
        assert assessment.score == .97 and assessment.preserves_meaning
        assert json.loads(requests[1]["messages"][1]["content"]) == {"original": "원문", "candidate": "후보"}
        assert budget.snapshot()["llm_input_tokens"] == 200
        assert budget.snapshot()["llm_output_tokens"] == 40
    run(scenario())


@pytest.mark.parametrize("content", [
    {"candidates": []},
    {"candidates": [{"text": "x", "changes": [], "major_change": "false"}]},
    {"candidates": [{"text": "", "changes": [], "major_change": False}]},
    {"candidates": [{"text": "x", "changes": "not a list", "major_change": False}]},
])
def test_malformed_llm_candidates_fail_closed(content):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=llm_response(content))
        )) as client:
            rewriter = OpenAIRewriter(AsyncTransport(client, Budget(Settings())), "key")
            with pytest.raises(APIError):
                await rewriter.generate("a", "a", 1, {})
    run(scenario())


def test_semantic_boolean_must_be_boolean_not_truthy_string():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=llm_response({
                "score": 1, "preserves_meaning": "false", "reason": "malformed"}))
        )) as client:
            with pytest.raises(APIError):
                await OpenAIRewriter(AsyncTransport(client, Budget(Settings())), "key").assess("a", "b")
    run(scenario())


@pytest.mark.parametrize("body", [
    {"choices": "wrong-type"},
    {"choices": ["wrong-type"]},
    {"choices": []},
    {"choices": [{"finish_reason": "stop", "message": "wrong-type"}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]},
    {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
])
def test_malformed_choices_return_sanitized_provider_error(body):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body)
        )) as client:
            with pytest.raises(APIError) as error:
                await OpenAIRewriter(AsyncTransport(client, Budget(Settings())), "key").assess("a", "b")
            assert "wrong-type" not in str(error.value)
            assert "not-json" not in str(error.value)
    run(scenario())


def test_live_missing_configuration_is_explicit(monkeypatch):
    for name in ["LLM_API_KEY", "OPENAI_API_KEY", "GPTZERO_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigurationError, match="LLM_API_KEY.*GPTZERO_API_KEY"):
        build_providers("live", Settings())


def test_mock_labels_are_explicit_and_no_real_requests_or_cost():
    async def scenario():
        detector, rewriter, budget, client = build_providers("mock", Settings())
        try:
            result = await detector.detect("내용 123")
            assert result.is_mock and "MOCK" in result.provider
            assert result == await detector.detect("내용 123")
            candidates = await rewriter.generate("내용 123", "내용 123", 3, {})
            assert len({candidate.text for candidate in candidates}) == 3
            assert all("Mock" in candidate.changes[0] for candidate in candidates)
            assert (await rewriter.assess("내용 123", candidates[0].text)).preserves_meaning
            assert not (await rewriter.assess("내용 123", "다른 내용")).preserves_meaning
            assert budget.snapshot()["api_calls"] == 0
            assert budget.snapshot()["reserved_cost_usd"] == 0
        finally:
            await client.aclose()
    run(scenario())


@pytest.mark.parametrize("key_name", ["LLM_API_KEY", "OPENAI_API_KEY"])
def test_live_editing_needs_only_openai_and_sends_only_openai_requests(monkeypatch, key_name):
    import textlab.providers as providers

    for name in ("LLM_API_KEY", "OPENAI_API_KEY", "GPTZERO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(key_name, "fake-editing-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    def forbidden_detector(*args, **kwargs):
        pytest.fail("The editing workflow must never construct a GPTZero detector")

    monkeypatch.setattr(providers, "GPTZeroDetector", forbidden_detector)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "api.openai.com"
        assert request.headers["authorization"] == "Bearer fake-editing-key"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4o-mini"
        if len(requests) == 1:
            return httpx.Response(200, json=llm_response({"candidates": [
                {"text": "개선한 본문입니다.", "changes": ["흐름 개선"], "major_change": False}]}))
        assert json.loads(payload["messages"][1]["content"]) == {
            "original": "원문입니다.", "candidate": "개선한 본문입니다."
        }
        return httpx.Response(200, json=llm_response({
            "score": .97, "preserves_meaning": True, "reason": "원문 의미 유지"}))

    real_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(providers.httpx, "AsyncClient", mocked_client)

    async def scenario():
        rewriter, budget, client = build_editing_providers("live", Settings(detector_request_cost=0))
        try:
            assert not rewriter.is_mock
            revisions = await rewriter.generate("원문입니다.", "원문입니다.", 1, {})
            assessment = await rewriter.assess("원문입니다.", revisions[0].text)
            assert assessment.preserves_meaning
            assert budget.snapshot()["api_calls"] == 2
            assert budget.snapshot()["reserved_cost_usd"] == .04
            assert len(requests) == 2
        finally:
            await client.aclose()

    run(scenario())


def test_editing_configuration_does_not_request_gptzero_key(monkeypatch):
    for name in ("LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GPTZERO_API_KEY", "unused-detector-key")

    with pytest.raises(ConfigurationError, match="LLM_API_KEY") as error:
        build_editing_providers("live", Settings())

    assert "GPTZERO" not in str(error.value)


def test_mock_editing_has_no_detector_requests_or_cost(monkeypatch):
    async def scenario():
        rewriter, budget, client = build_editing_providers("mock", Settings())
        try:
            assert rewriter.is_mock
            revisions = await rewriter.generate("내용 123", "내용 123", 1, {})
            assert (await rewriter.assess("내용 123", revisions[0].text)).preserves_meaning
            assert budget.snapshot()["api_calls"] == 0
            assert budget.snapshot()["reserved_cost_usd"] == 0
        finally:
            await client.aclose()
    run(scenario())


def test_editing_openai_403_identifies_service_without_leaking_response_or_key():
    async def scenario():
        budget = Budget(Settings())
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"error": "private-response-body"})
        )) as client:
            rewriter = OpenAIRewriter(AsyncTransport(client, budget), "private-api-key")
            with pytest.raises(APIError, match="OpenAI API HTTP 403") as error:
                await rewriter.generate("private-original", "private-original", 1, {})
        message = str(error.value)
        assert "private-response-body" not in message
        assert "private-api-key" not in message
        assert "private-original" not in message
        assert budget.snapshot()["api_calls"] == 1
    run(scenario())

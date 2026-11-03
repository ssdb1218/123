"""Bounded HTTP calls: preserve proxy/TLS defaults and never expose API bodies."""
from __future__ import annotations

import asyncio
import math
import threading
from typing import Any

import httpx

from textlab.models import Settings


class APIError(RuntimeError):
    """A sanitized user-visible provider failure."""


class ConfigurationError(ValueError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Reserve configured request ceilings before *each* attempt, including retries.

    Reservations are estimates supplied by the operator, not invoice amounts.
    An actual monetary hard cap must also be configured at the API provider.
    """

    def __init__(self, settings: Settings):
        settings.validate()
        self.settings = settings
        self.api_calls = 0
        self.reserved_cost_usd = 0.0
        self.llm_input_tokens = 0
        self.llm_output_tokens = 0
        self._lock = threading.Lock()

    def reserve(self, kind: str) -> None:
        cost = (self.settings.detector_request_cost if kind == "detector"
                else self.settings.llm_request_cost)
        with self._lock:
            if self.api_calls >= self.settings.max_api_calls:
                raise BudgetExceeded("API 호출 횟수 한도에 도달했습니다.")
            if self.reserved_cost_usd + cost > self.settings.max_cost_usd + 1e-12:
                raise BudgetExceeded("설정한 예상 API 비용 한도에 도달했습니다.")
            self.api_calls += 1
            self.reserved_cost_usd = round(self.reserved_cost_usd + cost, 10)

    def record_tokens(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        with self._lock:
            for name, key in [("llm_input_tokens", "prompt_tokens"),
                              ("llm_output_tokens", "completion_tokens")]:
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    setattr(self, name, getattr(self, name) + value)

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "api_calls": self.api_calls,
                "reserved_cost_usd": self.reserved_cost_usd,
                "max_api_calls": self.settings.max_api_calls,
                "max_cost_usd": self.settings.max_cost_usd,
                "llm_input_tokens": self.llm_input_tokens,
                "llm_output_tokens": self.llm_output_tokens,
            }


class AsyncTransport:
    def __init__(self, client: httpx.AsyncClient, budget: Budget,
                 *, max_attempts: int = 3, backoff_seconds: float = 0.5):
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be 1..5")
        self.client = client
        self.budget = budget
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds

    async def request_json(self, kind: str, method: str, url: str, *,
                           headers: dict[str, str], payload: dict[str, Any]) -> dict:
        for attempt in range(self.max_attempts):
            self.budget.reserve(kind)
            try:
                response = await self.client.request(method, url, headers=headers,
                                                     json=payload)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError):
                if attempt + 1 == self.max_attempts:
                    raise APIError("API 연결 또는 응답 시간 초과입니다. 네트워크·허용 도메인을 확인하세요.") from None
                await asyncio.sleep(self.backoff_seconds * 2 ** attempt)
                continue

            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt + 1 < self.max_attempts:
                    delay = self.backoff_seconds * 2 ** attempt
                    try:
                        retry_after = float(response.headers.get("retry-after", ""))
                        if math.isfinite(retry_after) and retry_after >= 0:
                            delay = max(delay, min(retry_after, 10))
                    except ValueError:
                        pass
                    await asyncio.sleep(min(delay, 10))
                    continue
            if not 200 <= response.status_code < 300:
                explanations = {
                    400: "입력 길이·언어·모델 또는 API 요청 조건을 확인하세요.",
                    401: "API 키 설정과 유효성을 확인하세요.",
                    403: "계정 권한·서비스 접근 정책·네트워크 허용 도메인을 확인하세요.",
                    404: "모델 사용 권한 또는 API 경로를 확인하세요.",
                    413: "텍스트가 서비스의 입력 한도를 초과했습니다.",
                    422: "서비스의 입력 조건을 확인하세요.",
                    429: "호출 제한 또는 계정 잔액을 확인하세요.",
                }
                hint = explanations.get(response.status_code, "서비스 상태를 확인하세요.")
                raise APIError(f"API HTTP {response.status_code}: {hint}")
            try:
                body = response.json()
            except (ValueError, UnicodeError):
                raise APIError("API가 유효한 JSON을 반환하지 않았습니다.") from None
            if not isinstance(body, dict):
                raise APIError("API 응답 JSON 형식이 올바르지 않습니다.")
            return body
        raise APIError("API 재시도 한도를 초과했습니다.")

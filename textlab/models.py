from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class Settings:
    target_score: float = 5.0
    max_iterations: int = 20
    candidates_per_iteration: int = 3
    patience: int = 3
    min_semantic_score: float = 0.90
    require_approval: bool = True
    major_change_threshold: float = 0.35
    protected_terms: list[str] = field(default_factory=list)
    max_api_calls: int = 200
    max_cost_usd: float = 5.0
    detector_request_cost: float = 0.01
    llm_request_cost: float = 0.02

    def validate(self) -> None:
        import math
        for name in ("target_score", "min_semantic_score", "major_change_threshold", "max_cost_usd",
                     "detector_request_cost", "llm_request_cost"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name}은 유한한 숫자여야 합니다.")
        for name in ("max_iterations", "candidates_per_iteration", "patience", "max_api_calls"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name}은 정수여야 합니다.")
        if type(self.require_approval) is not bool:
            raise ValueError("승인 설정은 참 또는 거짓이어야 합니다.")
        if not isinstance(self.protected_terms, list) or any(not isinstance(t, str) for t in self.protected_terms):
            raise ValueError("보호 용어는 문자열 목록이어야 합니다.")
        if not 0 <= self.target_score <= 100:
            raise ValueError("목표 점수는 0~100이어야 합니다.")
        if not 1 <= self.max_iterations <= 20:
            raise ValueError("반복 횟수는 1~20이어야 합니다.")
        if not 1 <= self.candidates_per_iteration <= 5 or not 1 <= self.patience <= 20:
            raise ValueError("후보는 1~5개, 조기 종료 기준은 1~20회여야 합니다.")
        if not 0 <= self.min_semantic_score <= 1 or not 0 <= self.major_change_threshold <= 1:
            raise ValueError("의미 보존 및 중요 수정 기준은 0~1이어야 합니다.")
        if self.max_api_calls < 1 or self.max_cost_usd <= 0:
            raise ValueError("API 호출 및 비용 한도는 양수여야 합니다.")
        if self.detector_request_cost < 0 or self.llm_request_cost < 0:
            raise ValueError("요청당 비용은 음수일 수 없습니다.")


@dataclass
class Detection:
    score: float
    provider: str
    is_mock: bool
    explanation: str = ""

    def __post_init__(self) -> None:
        import math
        if not math.isfinite(self.score) or not 0 <= self.score <= 100:
            raise ValueError("판독 응답 점수는 유효한 0~100 값이어야 합니다.")


@dataclass
class Revision:
    text: str
    changes: list[str]
    major_change: bool = False


@dataclass
class SemanticAssessment:
    score: float
    preserves_meaning: bool
    reason: str

    def __post_init__(self) -> None:
        import math
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("의미 보존 점수는 유효한 0~1 값이어야 합니다.")


@dataclass
class Candidate:
    id: str
    text: str
    changes: list[str]
    major_change: bool = False
    detection: Detection | None = None
    semantic: SemanticAssessment | None = None
    accepted: bool = False
    reason: str = ""


@dataclass
class Iteration:
    number: int
    candidates: list[Candidate] = field(default_factory=list)
    best_score: float | None = None
    adopted_id: str | None = None


@dataclass
class RunState:
    original_text: str
    settings: Settings
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_text: str = ""
    best_text: str = ""
    initial: Detection | None = None
    current: Detection | None = None
    best: Detection | None = None
    iterations: list[Iteration] = field(default_factory=list)
    status: str = "initializing"
    stop_reason: str = ""
    error: str = ""
    pending_candidate: Candidate | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

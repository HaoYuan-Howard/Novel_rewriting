"""运行统计的只读响应；缺用量与缺关联不能冒充零消耗。"""
from __future__ import annotations

from pydantic import Field

from contracts.schema.base import StrictModel


class UsageSummary(StrictModel):
    observed_rows: int = Field(ge=0)
    conflicting_rows: int = Field(ge=0)
    wire_calls: int = Field(ge=0)
    cached_calls: int = Field(ge=0)
    unknown_cache_calls: int = Field(ge=0)
    failed_calls: int = Field(ge=0)
    unknown_outcome_calls: int = Field(ge=0)
    missing_usage_calls: int = Field(ge=0)
    missing_latency_calls: int = Field(ge=0)
    known_prompt_tokens: int = Field(ge=0)
    known_completion_tokens: int = Field(ge=0)
    prompt_tokens: int | None = Field(ge=0)
    completion_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    reasoning_tokens: int | None = Field(ge=0)
    known_model_time_ms: int = Field(ge=0)
    model_time_ms: int | None = Field(ge=0)
    known_active_time_ms: int = Field(ge=0)
    active_time_ms: int | None = Field(ge=0)
    missing_interval_calls: int = Field(ge=0)


class AttemptMetrics(StrictModel):
    attempt_id: str
    turn: int
    run_id: str
    parent_run_id: str | None
    session_run_id: str | None
    state: str
    cached: bool | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int | None
    first_token_ms: int | None
    finished_at: str | None
    issue: str | None


class UnitMetrics(StrictModel):
    unit_id: str
    node: str
    role: str
    scope: str
    content_turns: int = Field(ge=0)
    network_retries: int = Field(ge=0)
    mechanical: int = Field(ge=0)
    cached_processing: int = Field(ge=0)
    usage: UsageSummary
    attempts: list[AttemptMetrics]
    issues: list[str]


class RunMetricChoice(StrictModel):
    run_id: str
    kind: str
    state: str
    started_at: int | None
    ended_at: int | None
    elapsed_seconds: int | None


class RunMetricsView(StrictModel):
    sid: str
    run_id: str | None
    runs: list[RunMetricChoice]
    usage: UsageSummary
    units: list[UnitMetrics]
    unassigned_rows: int = Field(ge=0)
    unassigned_usage: UsageSummary
    issues: list[str]

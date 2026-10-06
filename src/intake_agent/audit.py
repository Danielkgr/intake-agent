"""What a triage run records about itself: failures, audit events, usage, and the serving model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FailureKind = Literal[
    "refusal",
    "max_tokens",
    "invalid_output",
    "tool_error",
    "turn_cap",
    "api_error",
    "unexpected_stop",
]


class Failure(BaseModel):
    """Something that stopped the run from producing a record the firm can rely on."""

    kind: FailureKind
    detail: str


class AuditEvent(BaseModel):
    """One step of a run, in order: a model turn, a tool call, or a tool result."""

    turn: int
    event: Literal["model_turn", "tool_call", "tool_result", "note"]
    data: dict[str, Any] = Field(default_factory=dict)


class UsageSummary(BaseModel):
    """Token counts summed over every request in a run, with an estimated cost."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    estimated_cost_usd: float | None = None
    cost_basis: str = (
        "Estimated from the API's usage figures and published per-token prices.  "
        "The invoice is the authority."
    )


class ModelSummary(BaseModel):
    """Which model was asked for and which models actually answered."""

    requested: str
    effort: str
    fallbacks_enabled: bool
    answered_by: list[str] = Field(default_factory=list)
    fallback_used: bool = False

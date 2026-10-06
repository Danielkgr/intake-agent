"""The Claude triage agent: a tool-use loop that ends in a structured triage draft.

The loop uses the SDK's tool runner.  Each request carries the strict tool definitions and a JSON
output format, so the model can call tools on intermediate turns and must answer with a record
that matches the schema on its final turn.  The draft is validated only on that final turn.  Any
refusal, truncation, tool error, invalid output, API error, or exhausted turn cap becomes a
recorded failure rather than an exception, so the safety layer can route it to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

import anthropic
import pydantic
from anthropic import omit, transform_schema
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

from intake_agent.audit import AuditEvent, Failure, ModelSummary, UsageSummary
from intake_agent.conflicts import ConflictChecker, ConflictCheckResult, JsonConflictRegister
from intake_agent.policy import IntakePolicy, load_policy
from intake_agent.pricing import TokenCounts, cost_usd
from intake_agent.prompts import SYSTEM_PROMPT, build_user_message
from intake_agent.record import TriageRecord, assemble_record
from intake_agent.schema import Enquiry, TriageDraft
from intake_agent.tools import ToolContext

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# The schema sent as the output format.  The SDK moves constraints the API does not enforce,
# such as the three-question limit, into descriptions, and pydantic enforces them on the reply.
TRIAGE_OUTPUT_SCHEMA: dict[str, Any] = transform_schema(TriageDraft)


@dataclass(frozen=True)
class AgentSettings:
    model: str = DEFAULT_MODEL
    effort: Effort = "medium"
    max_turns: int = 8
    max_tokens: int = 16000
    # Server-side fallbacks suit the product path: a refused enquiry is retried on another model
    # instead of failing.  The evaluation turns them off, because a fallback would silently
    # change the model being measured.  It records refusals as their own outcome instead.
    fallbacks: bool = True


class AgentRun(BaseModel):
    """Everything one run produced, before the safety layer turns it into a triage record."""

    draft: TriageDraft | None
    failures: list[Failure]
    audit: list[AuditEvent]
    usage: UsageSummary
    model: ModelSummary
    conflict_results: list[ConflictCheckResult]


def _token_counts(message: BetaMessage) -> list[TokenCounts]:
    usage = message.usage
    if usage.iterations:
        return [
            TokenCounts(
                model=str(item.model or message.model),
                input_tokens=item.input_tokens,
                output_tokens=item.output_tokens,
                cache_creation_input_tokens=item.cache_creation_input_tokens,
                cache_read_input_tokens=item.cache_read_input_tokens,
            )
            for item in usage.iterations
            if item.type in ("message", "fallback_message")
        ]
    return [
        TokenCounts(
            model=str(message.model),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        )
    ]


def summarise_usage(counts: list[TokenCounts], requests: int) -> UsageSummary:
    costs = [cost_usd(item) for item in counts]
    return UsageSummary(
        requests=requests,
        input_tokens=sum(item.input_tokens for item in counts),
        output_tokens=sum(item.output_tokens for item in counts),
        cache_creation_input_tokens=sum(item.cache_creation_input_tokens for item in counts),
        cache_read_input_tokens=sum(item.cache_read_input_tokens for item in counts),
        estimated_cost_usd=None if any(cost is None for cost in costs) else round(sum(cast(list[float], costs)), 6),
    )


def _validation_summary(error: pydantic.ValidationError) -> str:
    problems = [f"{'.'.join(str(part) for part in item['loc']) or 'output'}: {item['msg']}" for item in error.errors()]
    return "The final output did not match the triage schema: " + "; ".join(problems[:5])


class ClaudeTriageAgent:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        settings: AgentSettings | None = None,
        policy: IntakePolicy | None = None,
        checker: ConflictChecker | None = None,
    ) -> None:
        self.client = client if client is not None else anthropic.Anthropic()
        self.settings = settings or AgentSettings()
        self.policy = policy or load_policy()
        self.checker = checker or ConflictChecker(JsonConflictRegister())

    def system_blocks(self) -> list[dict[str, Any]]:
        """The stable prefix.  With the tools ahead of it, the breakpoint caches tools and system."""
        return [
            {"type": "text", "text": SYSTEM_PROMPT},
            {
                "type": "text",
                "text": self.policy.summary_for_prompt(),
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def run(self, enquiry: Enquiry) -> AgentRun:
        settings = self.settings
        context = ToolContext(enquiry=enquiry, policy=self.policy, checker=self.checker)
        audit: list[AuditEvent] = []
        failures: list[Failure] = []
        counts: list[TokenCounts] = []
        answered_by: list[str] = []
        fallback_used = False
        last: BetaMessage | None = None
        turn = 0

        runner = self.client.beta.messages.tool_runner(
            model=settings.model,
            max_tokens=settings.max_tokens,
            system=cast(Any, self.system_blocks()),
            tools=context.runnable_tools(),
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": build_user_message(enquiry)}],
            output_config={
                "effort": settings.effort,
                "format": {"type": "json_schema", "schema": TRIAGE_OUTPUT_SCHEMA},
            },
            max_iterations=settings.max_turns,
            # Automatic caching for the growing conversation, on top of the explicit system breakpoint.
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA] if settings.fallbacks else omit,
            fallbacks="default" if settings.fallbacks else omit,
        )
        try:
            for message in runner:
                turn += 1
                last = message
                counts.extend(_token_counts(message))
                answered_by.append(str(message.model))
                served_by_fallback = any(item.type == "fallback_message" for item in message.usage.iterations or [])
                fallback_used = fallback_used or served_by_fallback
                tool_names: dict[str, str] = {}
                turn_data: dict[str, Any] = {
                    "model": str(message.model),
                    "stop_reason": message.stop_reason,
                    "served_by_fallback": served_by_fallback,
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                    "cache_read_input_tokens": message.usage.cache_read_input_tokens or 0,
                    "cache_creation_input_tokens": message.usage.cache_creation_input_tokens or 0,
                }
                if message.stop_reason == "refusal" and message.stop_details is not None:
                    turn_data["refusal_category"] = message.stop_details.category
                audit.append(AuditEvent(turn=turn, event="model_turn", data=turn_data))
                for block in message.content:
                    if block.type == "tool_use":
                        tool_names[block.id] = block.name
                        audit.append(
                            AuditEvent(
                                turn=turn,
                                event="tool_call",
                                data={"tool_use_id": block.id, "name": block.name, "input": block.input},
                            )
                        )
                if message.stop_reason != "tool_use":
                    continue
                response = runner.generate_tool_call_response()
                content = response["content"] if response is not None else []
                for item in cast(list[dict[str, Any]], content):
                    name = tool_names.get(str(item.get("tool_use_id")), "unknown")
                    is_error = bool(item.get("is_error", False))
                    audit.append(
                        AuditEvent(
                            turn=turn,
                            event="tool_result",
                            data={
                                "tool_use_id": item.get("tool_use_id"),
                                "name": name,
                                "is_error": is_error,
                                "content": item.get("content"),
                            },
                        )
                    )
                    if is_error:
                        failures.append(Failure(kind="tool_error", detail=f"{name}: {item.get('content')}"))
        except anthropic.RateLimitError as exc:
            failures.append(Failure(kind="api_error", detail=f"Rate limited after the SDK's retries: {exc.message}"))
        except anthropic.APIStatusError as exc:
            failures.append(Failure(kind="api_error", detail=f"HTTP {exc.status_code}: {exc.message}"))
        except anthropic.APIConnectionError as exc:
            failures.append(Failure(kind="api_error", detail=f"Could not reach the API: {exc}"))
        except anthropic.AnthropicError as exc:
            failures.append(Failure(kind="api_error", detail=f"The client could not make the request: {exc}"))

        draft = None
        api_failed = any(failure.kind == "api_error" for failure in failures)
        if last is not None and not api_failed:
            draft = self._read_final_turn(last, failures)

        return AgentRun(
            draft=draft,
            failures=failures,
            audit=audit,
            usage=summarise_usage(counts, requests=turn),
            model=ModelSummary(
                requested=settings.model,
                effort=settings.effort,
                fallbacks_enabled=settings.fallbacks,
                answered_by=answered_by,
                fallback_used=fallback_used,
            ),
            conflict_results=context.conflict_results,
        )

    def triage(self, enquiry: Enquiry) -> TriageRecord:
        """Run the agent, then apply the code-enforced checks and routing rules."""
        run = self.run(enquiry)
        return assemble_record(
            enquiry=enquiry,
            arm="claude",
            draft=run.draft,
            failures=run.failures,
            policy=self.policy,
            checker=self.checker,
            tool_conflict_results=run.conflict_results,
            model=run.model,
            usage=run.usage,
            audit=run.audit,
        )

    def _read_final_turn(self, last: BetaMessage, failures: list[Failure]) -> TriageDraft | None:
        stop = last.stop_reason
        if stop == "end_turn":
            text = "".join(block.text for block in last.content if block.type == "text").strip()
            try:
                return TriageDraft.model_validate_json(text)
            except pydantic.ValidationError as exc:
                failures.append(Failure(kind="invalid_output", detail=_validation_summary(exc)))
                return None
        if stop == "refusal":
            category = last.stop_details.category if last.stop_details is not None else None
            failures.append(Failure(kind="refusal", detail=f"The model declined to answer (category: {category})."))
        elif stop == "max_tokens":
            failures.append(Failure(kind="max_tokens", detail="The reply hit max_tokens, so the output is incomplete."))
        elif stop == "tool_use":
            failures.append(
                Failure(kind="turn_cap", detail=f"The run reached its cap of {self.settings.max_turns} turns.")
            )
        else:
            failures.append(Failure(kind="unexpected_stop", detail=f"The reply stopped with {stop!r}."))
        return None

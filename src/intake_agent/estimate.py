"""An estimate of what one enquiry costs on the Claude arm, before any run has measured it.

The prompt sizes are measured from the code: the tool definitions, the system prompt, the policy
summary, the output schema, the evaluation enquiries, and the tool results.  Converting
characters to tokens, and guessing the turns and output length, are assumptions.  They are
printed with the result.  A real run reports the real figures from the API's usage fields.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from intake_agent.agent import DEFAULT_MODEL, TRIAGE_OUTPUT_SCHEMA
from intake_agent.conflicts import ConflictChecker, JsonConflictRegister, PartyQuery
from intake_agent.evaluation import load_cases
from intake_agent.policy import load_policy
from intake_agent.pricing import PRICES_PER_MTOK
from intake_agent.prompts import SYSTEM_PROMPT, build_user_message
from intake_agent.schema import PartyRole, PracticeArea
from intake_agent.tools import TOOL_DEFINITIONS


@dataclass(frozen=True)
class Assumptions:
    chars_per_token: float = 3.0
    requests: int = 3
    output_tokens_per_tool_turn: int = 500
    output_tokens_final_turn: int = 1500
    tool_call_chars_per_turn: int = 400


@dataclass(frozen=True)
class PromptSizes:
    prefix_chars: int
    user_chars: int
    tool_result_chars_per_turn: int


def measure_prompt_sizes() -> PromptSizes:
    policy = load_policy()
    tools = json.dumps([{**tool, "strict": True} for tool in TOOL_DEFINITIONS])
    prefix = len(tools) + len(SYSTEM_PROMPT) + len(policy.summary_for_prompt()) + len(json.dumps(TRIAGE_OUTPUT_SCHEMA))
    cases = load_cases()
    user = round(sum(len(build_user_message(case.enquiry)) for case in cases) / len(cases))
    policy_result = round(
        sum(len(json.dumps(policy.area_for_model(area))) for area in PracticeArea) / len(PracticeArea)
    )
    checker = ConflictChecker(JsonConflictRegister())
    conflict_result = len(
        json.dumps(
            checker.check(
                [
                    PartyQuery(name="Enquirer Name", role=PartyRole.ENQUIRER),
                    PartyQuery(name="Other Side Pty Ltd", role=PartyRole.OPPOSING_PARTY),
                    PartyQuery(name="Another Person", role=PartyRole.OTHER_PARTY),
                ]
            ).for_model()
        )
    )
    return PromptSizes(
        prefix_chars=prefix,
        user_chars=user,
        tool_result_chars_per_turn=policy_result + conflict_result,
    )


def estimate_cost_usd(
    sizes: PromptSizes, assumptions: Assumptions, *, cached: bool, model: str = DEFAULT_MODEL
) -> float:
    """Sum the cost of each request in one enquiry's loop.

    With caching, the shared prefix is read from the cache, written by an earlier enquiry less
    than five minutes before, and each request writes only what the previous turn added.
    """
    price = PRICES_PER_MTOK[model]
    tokens = 1 / assumptions.chars_per_token
    prefix = sizes.prefix_chars * tokens
    user = sizes.user_chars * tokens
    turn_tail = (sizes.tool_result_chars_per_turn + assumptions.tool_call_chars_per_turn) * tokens
    total = 0.0
    history = 0.0
    added_last_turn = user
    for request in range(1, assumptions.requests + 1):
        final = request == assumptions.requests
        output = assumptions.output_tokens_final_turn if final else assumptions.output_tokens_per_tool_turn
        if cached:
            total += prefix * price.cache_read + history * price.cache_read + added_last_turn * price.cache_write_5m
        else:
            total += (prefix + history + added_last_turn) * price.input
        total += output * price.output
        history += added_last_turn
        # Earlier turns, thinking included, are sent back as input on the next request.
        added_last_turn = output + turn_tail
    return total / 1_000_000


def render_estimate() -> str:
    sizes = measure_prompt_sizes()
    typical = Assumptions()
    heavy = Assumptions(requests=4, output_tokens_per_tool_turn=1000, output_tokens_final_turn=3000)
    rows = []
    for label, assumptions in (("Typical", typical), ("Heavy", heavy)):
        rows.append(
            f"| {label} | {assumptions.requests} | {assumptions.output_tokens_per_tool_turn:,} | "
            f"{assumptions.output_tokens_final_turn:,} | "
            f"${estimate_cost_usd(sizes, assumptions, cached=True):.3f} | "
            f"${estimate_cost_usd(sizes, assumptions, cached=False):.3f} |"
        )
    tokens = 1 / typical.chars_per_token
    return "\n".join(
        [
            f"ESTIMATE, not a measurement.  {DEFAULT_MODEL} at $4 / $20 per million input / output tokens, "
            "cache reads $0.20, five-minute cache writes $5.",
            "",
            "| Measured from the code | Characters | Tokens at 3 characters each |",
            "|---|---|---|",
            f"| Cached prefix: tools, system prompt, policy summary, output schema | {sizes.prefix_chars:,} | "
            f"{sizes.prefix_chars * tokens:,.0f} |",
            f"| Average enquiry message over the evaluation set | {sizes.user_chars:,} | "
            f"{sizes.user_chars * tokens:,.0f} |",
            f"| Tool results per tool turn | {sizes.tool_result_chars_per_turn:,} | "
            f"{sizes.tool_result_chars_per_turn * tokens:,.0f} |",
            "",
            "| Scenario | Requests | Output tokens per tool turn | Output tokens, final turn | "
            "Per enquiry, warm cache | Per enquiry, no cache |",
            "|---|---|---|---|---|---|",
            *rows,
        ]
    )

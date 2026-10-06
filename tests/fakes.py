"""A scripted stand-in for the Messages API, served through httpx2's mock transport.

anthropic 1.x is built on httpx2, the maintained fork of httpx, so the mock transport and client
come from httpx2.  No test calls the real API.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import anthropic
import httpx2

from intake_agent.schema import Enquiry, EnquiryFields

ENQUIRY = Enquiry(
    enquiry_id="T-1",
    channel="web_form",
    received_date=date(2026, 9, 29),
    fields=EnquiryFields(name="Nadia Ferreira-Holt", email="nadia@example.com"),
    text=(
        "I was dismissed by Glasshouse Bakery Pty Ltd on 24 September 2026 after five years as a shift "
        "supervisor.  My manager Owen Treloar said it was for performance, but I never had a warning."
    ),
)

DEFAULT_USAGE = {
    "input_tokens": 400,
    "output_tokens": 300,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 3000,
}


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def thinking_block() -> dict[str, Any]:
    return {"type": "thinking", "thinking": "", "signature": "sig-1"}


def tool_use(tool_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def api_message(
    content: list[dict[str, Any]],
    stop_reason: str,
    *,
    model: str = "claude-opus-5-5",
    usage: dict[str, Any] | None = None,
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage or dict(DEFAULT_USAGE),
    }


def draft(**overrides: Any) -> dict[str, Any]:
    """A valid triage draft for ENQUIRY."""
    base: dict[str, Any] = {
        "matter_type": "Unfair dismissal",
        "practice_area": "employment",
        "urgency": "urgent",
        "urgency_reasons": ["The dismissal took effect 5 days before receipt, inside the 21-day window."],
        "parties": [
            {"name": "Nadia Ferreira-Holt", "role": "enquirer", "kind": "person"},
            {"name": "Glasshouse Bakery Pty Ltd", "role": "opposing_party", "kind": "organisation"},
            {"name": "Owen Treloar", "role": "other_party", "kind": "person"},
        ],
        "conflict_status": "clear",
        "clarifying_questions": ["Are you still being paid by the bakery?"],
        "practitioner_summary": [
            {
                "statement": "Dismissed by Glasshouse Bakery on 24 September 2026.",
                "source_quote": "I was dismissed by Glasshouse Bakery Pty Ltd on 24 September 2026",
            },
            {
                "statement": "Says no warning was given before the dismissal.",
                "source_quote": "I never had a warning",
            },
        ],
        "holding_reply": (
            "Thank you for contacting Quollridge Lawyers.  We have received your enquiry, and a member of "
            "our employment team will contact you today.  If you have a hearing in the next few days, "
            "please also telephone our office."
        ),
        "routing": "urgent_human_review",
        "routing_reason": "An urgent employment matter.",
    }
    base.update(overrides)
    return base


def final_answer(**overrides: Any) -> dict[str, Any]:
    return api_message([text_block(json.dumps(draft(**overrides)))], "end_turn")


def tool_turn(*calls: dict[str, Any]) -> dict[str, Any]:
    return api_message([thinking_block(), *calls], "tool_use")


PARTIES = [
    {"name": "Nadia Ferreira-Holt", "role": "enquirer", "kind": "person"},
    {"name": "Glasshouse Bakery Pty Ltd", "role": "opposing_party", "kind": "organisation"},
    {"name": "Owen Treloar", "role": "other_party", "kind": "person"},
]


class ScriptedAPI:
    """Answers each POST to the Messages API with the next scripted response."""

    def __init__(self, *responses: dict[str, Any] | tuple[int, dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        if not self.responses:
            return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "done"}})
        item = self.responses.pop(0)
        if isinstance(item, tuple):
            status, body = item
            return httpx2.Response(status, json=body)
        return httpx2.Response(200, json=item)

    def client(self) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(self.handler)
        return anthropic.Anthropic(api_key="test", max_retries=0, http_client=httpx2.Client(transport=transport))

    def tool_results(self, request_index: int) -> list[dict[str, Any]]:
        """The tool_result blocks sent in the last user message of a request."""
        content = self.requests[request_index]["messages"][-1]["content"]
        return [block for block in content if block.get("type") == "tool_result"]

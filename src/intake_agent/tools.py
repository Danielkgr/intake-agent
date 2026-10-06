"""The client tools the agent can call, defined with strict JSON schemas.

Every tool is read-only.  None of them can contact the enquirer, change a record, or reach the
network, so a successful prompt injection can at worst distort the triage record, which the
code-enforced checks then constrain.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

from anthropic import beta_tool
from anthropic.lib.tools import BetaFunctionTool, ToolError

from intake_agent.conflicts import ConflictChecker, ConflictCheckResult, PartyQuery, RegisterUnavailableError
from intake_agent.policy import IntakePolicy
from intake_agent.schema import Enquiry, PartyKind, PartyRole, PracticeArea

CHECK_CONFLICTS = {
    "name": "check_conflicts",
    "description": (
        "Check names against the firm's conflict register.  Call it once with every person and "
        "organisation named in the enquiry or form fields, including the enquirer, before you write "
        "the record.  The tool decides whether each name matches the register and whether a match "
        "is a potential conflict.  Report its status as given."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "parties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The name exactly as written."},
                        "role": {"type": "string", "enum": [role.value for role in PartyRole]},
                        "kind": {"type": "string", "enum": [kind.value for kind in PartyKind]},
                    },
                    "required": ["name", "role", "kind"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["parties"],
        "additionalProperties": False,
    },
}

GET_INTAKE_POLICY = {
    "name": "get_intake_policy",
    "description": (
        "Return the firm's intake rules for one practice area: what it covers and refers elsewhere, "
        "its urgency triggers, and the information a practitioner needs.  Call it before you decide "
        "urgency, routing, or clarifying questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"practice_area": {"type": "string", "enum": [area.value for area in PracticeArea]}},
        "required": ["practice_area"],
        "additionalProperties": False,
    },
}

DAYS_FROM_RECEIPT = {
    "name": "days_from_receipt",
    "description": (
        "Compare a calendar date from the enquiry with the date the enquiry was received.  Returns "
        "the weekday, the days from receipt (negative for past dates), and the weekdays from receipt.  "
        "Use it for every date that could affect urgency instead of calculating."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"date": {"type": "string", "format": "date", "description": "YYYY-MM-DD"}},
        "required": ["date"],
        "additionalProperties": False,
    },
}

TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (GET_INTAKE_POLICY, CHECK_CONFLICTS, DAYS_FROM_RECEIPT)


def weekdays_between(start: dt.date, end: dt.date) -> int:
    """Signed count of Monday-to-Friday days after start up to and including end.

    Public holidays are not counted out, so this can overstate business days.
    """
    step = 1 if end >= start else -1
    count, day = 0, start
    while day != end:
        day += dt.timedelta(days=step)
        if day.weekday() < 5:
            count += step
    return count


@dataclass
class ToolContext:
    """What the tools know about one enquiry, and what they found."""

    enquiry: Enquiry
    policy: IntakePolicy
    checker: ConflictChecker
    conflict_results: list[ConflictCheckResult] = field(default_factory=list)

    def check_conflicts(self, parties: list[PartyQuery]) -> str:
        try:
            result = self.checker.check(parties)
        except RegisterUnavailableError as exc:
            raise ToolError(
                "The conflict register is unavailable, so the conflict check did not run."
            ) from exc
        self.conflict_results.append(result)
        return json.dumps(result.for_model())

    def get_intake_policy(self, practice_area: PracticeArea) -> str:
        return json.dumps(self.policy.area_for_model(practice_area))

    def days_from_receipt(self, date: str) -> str:
        try:
            target = dt.date.fromisoformat(date)
        except ValueError as exc:
            raise ToolError(f"{date!r} is not a valid calendar date in YYYY-MM-DD form.") from exc
        received = self.enquiry.received_date
        return json.dumps(
            {
                "date": target.isoformat(),
                "weekday": f"{target:%A}",
                "received_date": received.isoformat(),
                "days_from_receipt": (target - received).days,
                "weekdays_from_receipt": weekdays_between(received, target),
                "note": "Weekdays do not exclude public holidays.",
            }
        )

    def runnable_tools(self) -> list[BetaFunctionTool[Any]]:
        implementations = {
            "check_conflicts": self.check_conflicts,
            "get_intake_policy": self.get_intake_policy,
            "days_from_receipt": self.days_from_receipt,
        }
        return [
            beta_tool(
                implementations[definition["name"]],
                name=definition["name"],
                description=definition["description"],
                input_schema=definition["input_schema"],
                strict=True,
            )
            for definition in TOOL_DEFINITIONS
        ]

"""The firm's intake policy: which matters it takes, what makes them urgent, and how to route them."""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from intake_agent.schema import PracticeArea


class FirmInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fictional: bool
    policy_version: str
    location: str
    standard_response: str
    urgent_response: str


class GeneralRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[str]
    out_of_scope_areas: list[str]


class UrgencyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgent: str
    time_sensitive: str
    routine: str
    general_urgent_triggers: list[str]


class RoutingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_consult: str
    conflicts_partner_review: str
    decline_and_refer: str
    urgent_human_review: str


class AreaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    covers: list[str]
    refers: list[str]
    urgency_triggers: list[str]
    required_information: list[str]


class IntakePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm: FirmInfo
    general: GeneralRules
    urgency: UrgencyRules
    routing: RoutingRules
    areas: dict[PracticeArea, AreaPolicy]

    def area(self, practice_area: PracticeArea) -> AreaPolicy:
        return self.areas[practice_area]

    def area_for_model(self, practice_area: PracticeArea) -> dict[str, Any]:
        """The detailed rules for one area, as the get_intake_policy tool returns them."""
        area = self.area(practice_area)
        result: dict[str, Any] = {
            "practice_area": practice_area.value,
            "label": area.label,
            "firm_handles": practice_area is not PracticeArea.OUT_OF_SCOPE,
            "covers": area.covers,
            "refers_elsewhere": area.refers,
            "urgency_triggers": area.urgency_triggers,
            "required_information": area.required_information,
        }
        if practice_area is PracticeArea.OUT_OF_SCOPE:
            result["out_of_scope_areas"] = self.general.out_of_scope_areas
        return result

    def summary_for_prompt(self) -> str:
        """The firm-wide policy as stable text for the cached system prompt.

        The output depends only on the policy file, so it is byte-identical across requests.
        """
        lines = [
            f"# Intake policy for {self.firm.name} (fictional), version {self.firm.policy_version}",
            "",
            "## General rules",
            *[f"- {rule}" for rule in self.general.rules],
            "",
            "## Practice areas",
        ]
        for practice_area in PracticeArea:
            if practice_area is PracticeArea.OUT_OF_SCOPE:
                continue
            area = self.area(practice_area)
            lines.append(f"- {practice_area.value} ({area.label}): covers {'; '.join(area.covers)}.")
            if area.refers:
                lines.append(f"  Refers elsewhere: {'; '.join(area.refers)}.")
        lines += [
            f"- out_of_scope: {'; '.join(self.general.out_of_scope_areas)}.",
            "",
            "## Urgency levels",
            f"- urgent: {self.urgency.urgent}",
            f"- time_sensitive: {self.urgency.time_sensitive}",
            f"- routine: {self.urgency.routine}",
            "General urgent triggers:",
            *[f"- {trigger}" for trigger in self.urgency.general_urgent_triggers],
            "",
            "## Routing",
            f"- book_consult: {self.routing.book_consult}",
            f"- conflicts_partner_review: {self.routing.conflicts_partner_review}",
            f"- decline_and_refer: {self.routing.decline_and_refer}",
            f"- urgent_human_review: {self.routing.urgent_human_review}",
            "",
            f"Response times: standard enquiries {self.firm.standard_response}, urgent enquiries "
            f"{self.firm.urgent_response}.",
        ]
        return "\n".join(lines)


def load_policy(path: Path | None = None) -> IntakePolicy:
    if path is None:
        text = resources.files("intake_agent").joinpath("data/policy.toml").read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return IntakePolicy.model_validate(tomllib.loads(text))

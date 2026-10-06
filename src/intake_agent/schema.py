"""Shared vocabulary for enquiries, parties, and triage decisions."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PracticeArea(StrEnum):
    EMPLOYMENT = "employment"
    FAMILY = "family"
    PROPERTY_CONVEYANCING = "property_conveyancing"
    WILLS_ESTATES = "wills_estates"
    COMMERCIAL_DISPUTES = "commercial_disputes"
    PERSONAL_INJURY = "personal_injury"
    CRIMINAL = "criminal"
    OUT_OF_SCOPE = "out_of_scope"


class Urgency(StrEnum):
    URGENT = "urgent"
    TIME_SENSITIVE = "time_sensitive"
    ROUTINE = "routine"


class ConflictStatus(StrEnum):
    CLEAR = "clear"
    POTENTIAL_CONFLICT = "potential_conflict"
    NOT_CHECKED = "not_checked"


class Routing(StrEnum):
    BOOK_CONSULT = "book_consult"
    CONFLICTS_PARTNER_REVIEW = "conflicts_partner_review"
    DECLINE_AND_REFER = "decline_and_refer"
    URGENT_HUMAN_REVIEW = "urgent_human_review"


class PartyRole(StrEnum):
    ENQUIRER = "enquirer"
    OPPOSING_PARTY = "opposing_party"
    OTHER_PARTY = "other_party"


class PartyKind(StrEnum):
    PERSON = "person"
    ORGANISATION = "organisation"
    UNKNOWN = "unknown"


class EnquiryFields(BaseModel):
    """Optional structured fields from a web form."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    organisation: str | None = None
    stated_matter: str | None = None


class Enquiry(BaseModel):
    """One enquiry as received.  The text and fields are untrusted input from the public."""

    model_config = ConfigDict(extra="forbid")

    enquiry_id: str = "enquiry"
    channel: Literal["web_form", "email"] = "web_form"
    received_date: date
    fields: EnquiryFields = Field(default_factory=EnquiryFields)
    text: str


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The name exactly as the enquiry or form writes it.")
    role: PartyRole
    kind: PartyKind


class SummaryStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(description="One fact from the enquiry, in plain words, for a practitioner.")
    source_quote: str = Field(
        description="Words copied exactly from the enquiry or form fields that support the statement."
    )


class TriageDraft(BaseModel):
    """The structured answer the triage step produces, before the code-enforced safety checks."""

    model_config = ConfigDict(extra="forbid")

    matter_type: str = Field(description="A short label, such as 'Unfair dismissal' or 'Purchase of a house'.")
    practice_area: PracticeArea
    urgency: Urgency
    urgency_reasons: list[str] = Field(description="The policy trigger and the fact behind it, one per item.")
    parties: list[Party]
    conflict_status: ConflictStatus = Field(description="The status check_conflicts returned.")
    clarifying_questions: list[str] = Field(
        max_length=3, description="At most three questions to the enquirer for required information."
    )
    practitioner_summary: list[SummaryStatement] = Field(min_length=1)
    holding_reply: str = Field(description="The reply to the enquirer.  It never gives legal advice.")
    routing: Routing
    routing_reason: str

"""Shared vocabulary for enquiries, parties, and triage decisions."""

from __future__ import annotations

from enum import StrEnum


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

"""Deterministic checks that run after the triage step, whatever produced the draft.

None of these checks uses a model.  Each one fails closed: when it trips, the enquiry goes to a
person instead of out of the door.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from intake_agent.policy import IntakePolicy
from intake_agent.schema import SummaryStatement

# Phrases that give, or look like, legal advice.  The list errs towards flagging, because a
# flagged reply costs a person a minute and an unflagged piece of advice can cost far more.
_ADVICE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("tells the enquirer what to do", r"\byou\s+(?:should|must|ought\s+to|need\s+to|have\s+to)\b"),
        (
            "recommends a course",
            r"\b(?:we|i)\s+(?:would\s+)?(?:strongly\s+)?(?:recommend|advise|suggest\s+that)\b",
        ),
        ("offers advice", r"\b(?:my|our)\s+(?:legal\s+)?advice\b"),
        (
            "states an entitlement or liability",
            r"\byou\s+(?:are|were|may\s+be|might\s+be|would\s+be|could\s+be)\s+(?:not\s+)?"
            r"(?:entitled|eligible|liable|responsible|protected)\b",
        ),
        (
            "assesses the merits",
            r"\b(?:a\s+)?(?:strong|good|valid|weak|reasonable|solid|winnable)\s+(?:case|claim|defence|defense)\b",
        ),
        ("predicts an outcome", r"\b(?:likely|unlikely)\s+to\s+(?:succeed|win|lose|be\s+successful)\b"),
        (
            "predicts an outcome",
            r"\byou\s+(?:will|would|could|can)\s+(?:win|succeed|lose|recover|sue|be\s+compensated|get\s+compensation)\b",
        ),
        (
            "states a legal conclusion",
            r"\b(?:is|was|were|are)\s+(?:clearly\s+)?(?:unlawful|illegal|invalid|void|in\s+breach)\b",
        ),
        ("cites a provision", r"(?:^|(?<=\s))(?:s|ss|section|sections|reg|regulation)\s+\d+"),
        ("cites the law", r"\bunder\s+the\s+(?:law|act|legislation|regulations?)\b"),
        ("cites the law", r"\bthe\s+law\s+(?:says|requires|provides|allows|permits|states)\b"),
        (
            "mentions a legal time limit",
            r"\b(?:time\s+limit|limitation\s+period|statute\s+of\s+limitations)\b",
        ),
        (
            "tells the enquirer what not to do",
            r"\b(?:do\s+not|don't|never)\s+(?:sign|agree\s+to|pay|admit|accept|plead|resign|vacate|settle)\b",
        ),
        ("promises an outcome", r"\b(?:guarantee|guaranteed|certainly\s+will)\b"),
        ("states the enquirer's rights", r"\byour\s+(?:legal\s+)?rights\s+(?:are|include)\b"),
    )
)
_ACT_NAME = re.compile(r"\b[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*\s+Act\s+(?:18|19|20)\d{2}\b")


class AdviceCheck(BaseModel):
    flagged: bool
    matches: list[str] = Field(default_factory=list)
    checked_text: str = ""


def check_advice(text: str) -> AdviceCheck:
    """Scan a holding reply for language that gives or resembles legal advice."""
    matches = [
        f"{label}: {m.group(0).strip()!r}" for label, pattern in _ADVICE_PATTERNS for m in pattern.finditer(text)
    ]
    matches += [f"names legislation: {m.group(0)!r}" for m in _ACT_NAME.finditer(text)]
    return AdviceCheck(flagged=bool(matches), matches=matches, checked_text=text)


_QUOTE_CHARS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"})


def _comparable(text: str) -> str:
    return " ".join(text.translate(_QUOTE_CHARS).casefold().split())


class GroundingCheck(BaseModel):
    passed: bool
    unsupported: list[str] = Field(default_factory=list)


def check_grounding(statements: list[SummaryStatement], sources: list[str]) -> GroundingCheck:
    """Every summary statement must carry a quote that appears in the enquiry or its form fields."""
    haystack = "\n".join(_comparable(source) for source in sources)
    unsupported = [
        statement.statement
        for statement in statements
        if not (quote := _comparable(statement.source_quote).strip(" .,;:!?\"'")) or quote not in haystack
    ]
    return GroundingCheck(passed=not unsupported, unsupported=unsupported)


def conflict_reply(policy: IntakePolicy) -> str:
    """A reply that says nothing about the matter, for use while a potential conflict is open."""
    return (
        f"Thank you for contacting {policy.firm.name}.  We have received your enquiry.  Before we can "
        f"discuss it, we need to complete some internal checks, and someone from the firm will contact "
        f"you {policy.firm.standard_response}.  Please do not send further details or documents until "
        f"we have been in touch."
    )


def review_reply(policy: IntakePolicy) -> str:
    """A reply for any enquiry a person must review before the firm says more."""
    return (
        f"Thank you for contacting {policy.firm.name}.  We have received your enquiry, and a member of "
        f"our team will review it and contact you {policy.firm.urgent_response}.  If you have a court "
        f"date or deadline in the next few days, please also telephone our office."
    )

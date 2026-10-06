"""A transparent keyword and rules triage, for comparison with the Claude agent.

It reads the same enquiries, uses the same conflict check, and goes through the same safety layer.
Every rule is written out below.  The author wrote both these rules and the evaluation data, so
the baseline is an optimistic reference point, not a fair competitor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from intake_agent.conflicts import ConflictChecker, JsonConflictRegister, PartyQuery, RegisterUnavailableError
from intake_agent.extract import candidate_names
from intake_agent.policy import IntakePolicy, load_policy
from intake_agent.record import TriageRecord, assemble_record
from intake_agent.schema import (
    ConflictStatus,
    Enquiry,
    Party,
    PartyKind,
    PartyRole,
    PracticeArea,
    Routing,
    SummaryStatement,
    TriageDraft,
    Urgency,
)

# Weighted keywords per area.  Phrases score 3, single words 1.
_AREA_KEYWORDS: dict[PracticeArea, tuple[tuple[str, int], ...]] = {
    PracticeArea.EMPLOYMENT: (
        ("unfair dismissal", 3),
        ("dismissed", 3),
        ("let go", 3),
        ("sacked", 3),
        ("redundan", 2),
        ("award rate", 3),
        ("payslip", 2),
        ("employer", 2),
        ("roster", 2),
        ("supervisor", 1),
        ("manager", 1),
        ("discriminat", 2),
        ("bullying", 2),
        ("wages", 2),
        ("underpaid", 3),
        ("my job", 2),
    ),
    PracticeArea.FAMILY: (
        ("divorce", 3),
        ("separated", 3),
        ("separation", 3),
        ("ex-partner", 3),
        ("former partner", 3),
        ("former husband", 3),
        ("former wife", 3),
        ("my wife", 2),
        ("my husband", 2),
        ("parenting", 3),
        ("intervention order", 3),
        ("custody of", 2),
        ("daughter", 1),
        ("son", 1),
        ("children", 1),
    ),
    PracticeArea.PROPERTY_CONVEYANCING: (
        ("contract to buy", 3),
        ("contract of sale", 3),
        ("settlement", 2),
        ("settle on", 3),
        ("vendor", 3),
        ("buyers", 2),
        ("lease", 3),
        ("landlord", 3),
        ("townhouse", 2),
        ("own a house together", 3),
        ("offer on our house", 3),
        ("cooling-off", 3),
        ("conveyanc", 3),
    ),
    PracticeArea.WILLS_ESTATES: (
        ("my will", 3),
        ("probate", 3),
        ("executor", 3),
        ("estate", 2),
        ("power of attorney", 3),
        ("died", 2),
        ("left everything", 3),
        ("inheritance", 3),
    ),
    PracticeArea.COMMERCIAL_DISPUTES: (
        ("invoice", 3),
        ("owe us", 3),
        ("statutory demand", 3),
        ("business partner", 3),
        ("partnership", 3),
        ("shareholder", 3),
        ("purchase order", 3),
        ("our company", 2),
        ("money back", 2),
        ("we paid", 2),
        ("engaged", 1),
        ("debt", 2),
    ),
    PracticeArea.PERSONAL_INJURY: (
        ("injur", 3),
        ("accident", 3),
        ("rear-ended", 3),
        ("slipped", 3),
        ("broke my", 3),
        ("hurt my", 3),
        ("surgery", 2),
        ("pain", 2),
        ("claim form", 2),
    ),
    PracticeArea.CRIMINAL: (
        ("charged with", 3),
        ("arrested", 3),
        ("bail", 3),
        ("drink driving", 3),
        ("police station", 3),
        ("offence", 2),
        ("trafficking", 3),
        ("plead", 2),
    ),
    PracticeArea.OUT_OF_SCOPE: (
        ("visa", 3),
        ("immigration", 3),
        ("migration", 3),
        ("trade mark", 3),
        ("trademark", 3),
        ("copyright", 3),
        ("patent", 3),
        ("ato", 3),
        ("tax return", 3),
        ("bankrupt", 3),
        ("planning permit", 3),
    ),
}

# Matters the policy says a handled area refers elsewhere.
_REFERRED = re.compile(
    r"\b(?:county court|supreme court|indictable|commercial quantity|medical negligence|vcat)\b", re.IGNORECASE
)
_SAFETY = re.compile(
    r"\b(?:intervention order|family violence|scared|afraid|threatened|in custody|been arrested|was arrested|"
    r"held at the police|bail hearing|bail application)\b",
    re.IGNORECASE,
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
_MONTH_PATTERN = "|".join(_MONTHS)
_FULL_DATE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\b", re.IGNORECASE)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_PATTERN})\s+(\d{{4}})\b", re.IGNORECASE)
_RELATIVE = {"yesterday": -1, "last night": 0, "tonight": 0, "today": 0, "tomorrow": 1, "next week": 7, "last week": -7}
_SIGN_OFF = re.compile(r"^(?:kind\s+regards|regards|thanks|thank\s+you|cheers|sincerely|best),?\s*$", re.IGNORECASE)
_ORGANISATION_HINT = re.compile(r"\b(?:pty|ltd|limited|inc|group|holdings|co)\b\.?", re.IGNORECASE)


@dataclass(frozen=True)
class DatedEvent:
    when: date
    sentence: str


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _resolve_year(day: int, month: int, received: date) -> date | None:
    """A date written without a year is taken as the one closest to the day the enquiry arrived."""
    options = []
    for year in (received.year - 1, received.year, received.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:
            continue
    return min(options, key=lambda option: abs((option - received).days)) if options else None


def dated_events(text: str, received: date) -> list[DatedEvent]:
    events: list[DatedEvent] = []
    for sentence in _sentences(text):
        lowered = sentence.casefold()
        for match in _FULL_DATE.finditer(sentence):
            day, month = int(match.group(1)), _MONTHS[match.group(2).casefold()]
            when: date | None
            if match.group(3):
                try:
                    when = date(int(match.group(3)), month, day)
                except ValueError:
                    when = None
            else:
                when = _resolve_year(day, month, received)
            if when is not None:
                events.append(DatedEvent(when, lowered))
        for match in _MONTH_YEAR.finditer(sentence):
            if not _FULL_DATE.search(sentence[max(0, match.start() - 3) : match.end()]):
                events.append(DatedEvent(date(int(match.group(2)), _MONTHS[match.group(1).casefold()], 1), lowered))
        for phrase, offset in _RELATIVE.items():
            if re.search(rf"\b{phrase}\b", lowered):
                events.append(DatedEvent(received + timedelta(days=offset), lowered))
    return events


def _has(sentence: str, *words: str) -> bool:
    return any(word in sentence for word in words)


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    for day in (start.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(start)


def assess_urgency(area: PracticeArea, text: str, received: date) -> tuple[Urgency, list[str]]:
    """Apply the policy's urgency triggers to the dates and phrases the rules can see."""
    levels: list[tuple[Urgency, str]] = []
    if _SAFETY.search(text):
        levels.append((Urgency.URGENT, "Mentions a risk to safety, an intervention order, custody, or bail."))
    for event in dated_events(text, received):
        days = (event.when - received).days
        s = event.sentence
        if area is PracticeArea.EMPLOYMENT and days <= 0 and _has(s, "dismiss", "let go", "sacked", "took effect"):
            if -days <= 21:
                levels.append((Urgency.URGENT, f"Dismissal {-days} days before receipt, inside 21 days."))
            elif -days <= 120:
                levels.append((Urgency.TIME_SENSITIVE, f"Dismissal {-days} days before receipt."))
        elif area is PracticeArea.FAMILY and _has(s, "divorce"):
            left = (_add_months(event.when, 12) - received).days
            if 0 <= left <= 42:
                levels.append((Urgency.URGENT, f"Property settlement limit {left} days away."))
            elif 0 <= left <= 120:
                levels.append((Urgency.TIME_SENSITIVE, f"Property settlement limit {left} days away."))
        elif area is PracticeArea.WILLS_ESTATES and _has(s, "probate", "letters of administration", "granted"):
            left = (_add_months(event.when, 6) - received).days
            if 0 <= left <= 42:
                levels.append((Urgency.URGENT, f"Family provision limit {left} days away."))
            elif 0 <= left <= 120:
                levels.append((Urgency.TIME_SENSITIVE, f"Family provision limit {left} days away."))
        elif area is PracticeArea.COMMERCIAL_DISPUTES and _has(s, "statutory demand") and -21 <= days <= 0:
            levels.append((Urgency.URGENT, f"Statutory demand served {-days} days before receipt."))
        elif area is PracticeArea.PERSONAL_INJURY and days < 0 and (received - event.when).days > 912:
            levels.append((Urgency.URGENT, "Injury more than 2 years and 6 months before receipt."))
        elif area is PracticeArea.PROPERTY_CONVEYANCING and _has(s, "signed") and -3 <= days <= 0:
            levels.append((Urgency.URGENT, "Contract signed within the last 3 days, possibly in cooling-off."))
        if days >= 0 and _has(s, "court", "hearing", "appear", "settle", "mention"):
            if days <= 7:
                levels.append((Urgency.URGENT, f"A hearing, court, or settlement date {days} days away."))
            elif days <= 42:
                levels.append((Urgency.TIME_SENSITIVE, f"A hearing, court, or settlement date {days} days away."))
    for level in (Urgency.URGENT, Urgency.TIME_SENSITIVE):
        reasons = [reason for found, reason in levels if found is level]
        if reasons:
            return level, reasons
    return Urgency.ROUTINE, ["No urgency trigger found in the text."]


def classify_area(text: str) -> PracticeArea:
    lowered = f" {text.casefold()} "
    scores = {
        area: sum(weight for keyword, weight in keywords if re.search(rf"\b{re.escape(keyword)}", lowered))
        for area, keywords in _AREA_KEYWORDS.items()
    }
    best = max(scores.values())
    if best == 0:
        return PracticeArea.OUT_OF_SCOPE
    return next(area for area in PracticeArea if scores[area] == best)


def find_parties(enquiry: Enquiry) -> list[Party]:
    parties: list[Party] = []
    if enquiry.fields.name:
        parties.append(Party(name=enquiry.fields.name, role=PartyRole.ENQUIRER, kind=PartyKind.PERSON))
    if enquiry.fields.organisation:
        parties.append(Party(name=enquiry.fields.organisation, role=PartyRole.ENQUIRER, kind=PartyKind.ORGANISATION))
    lines = [line.strip() for line in enquiry.text.splitlines() if line.strip()]
    signer = lines[-1] if len(lines) >= 2 and _SIGN_OFF.match(lines[-2]) else None
    if signer and not enquiry.fields.name:
        parties.append(Party(name=signer, role=PartyRole.ENQUIRER, kind=PartyKind.PERSON))
    known = {party.name for party in parties}
    for name in candidate_names(enquiry.text):
        if name in known:
            continue
        kind = PartyKind.ORGANISATION if _ORGANISATION_HINT.search(name) else PartyKind.UNKNOWN
        parties.append(Party(name=name, role=PartyRole.OTHER_PARTY, kind=kind))
    return parties


def clarifying_questions(area: PracticeArea, text: str, parties: list[Party], received: date) -> list[str]:
    questions: list[str] = []
    if area is PracticeArea.OUT_OF_SCOPE:
        return questions
    if not any(party.role is not PartyRole.ENQUIRER for party in parties):
        questions.append("What is the full name of the other person or business involved?")
    if not dated_events(text, received):
        questions.append("When did the main events happen, and is there any date coming up, such as a court date?")
    if area is PracticeArea.COMMERCIAL_DISPUTES and "$" not in text:
        questions.append("How much money is in dispute?")
    return questions[:3]


class RulesBaseline:
    def __init__(self, policy: IntakePolicy | None = None, checker: ConflictChecker | None = None) -> None:
        self.policy = policy or load_policy()
        self.checker = checker or ConflictChecker(JsonConflictRegister())

    def _reply(self, routing: Routing, area: PracticeArea) -> str:
        firm = self.policy.firm
        if routing is Routing.DECLINE_AND_REFER:
            return (
                f"Thank you for contacting {firm.name}.  We have received your enquiry.  Our firm does not act in "
                f"this kind of matter, so we are not able to help with it, and we suggest you look for a lawyer "
                f"who practises in this area."
            )
        if routing is Routing.URGENT_HUMAN_REVIEW:
            return (
                f"Thank you for contacting {firm.name}.  We have received your enquiry, and a member of our team "
                f"will contact you {firm.urgent_response}."
            )
        return (
            f"Thank you for contacting {firm.name}.  We have received your enquiry about a "
            f"{self.policy.area(area).label.lower()} matter, and someone from our team will contact you "
            f"{firm.standard_response} to arrange a time to talk."
        )

    def draft(self, enquiry: Enquiry) -> TriageDraft:
        text = enquiry.text
        area = classify_area(text)
        urgency, reasons = assess_urgency(area, text, enquiry.received_date)
        parties = find_parties(enquiry)
        try:
            status = self.checker.check([PartyQuery(name=p.name, role=p.role, kind=p.kind) for p in parties]).status
        except RegisterUnavailableError:
            status = ConflictStatus.NOT_CHECKED
        if status is ConflictStatus.POTENTIAL_CONFLICT:
            routing = Routing.CONFLICTS_PARTNER_REVIEW
        elif area is PracticeArea.OUT_OF_SCOPE or _REFERRED.search(text):
            routing = Routing.DECLINE_AND_REFER
        elif urgency is Urgency.URGENT:
            routing = Routing.URGENT_HUMAN_REVIEW
        else:
            routing = Routing.BOOK_CONSULT
        summary = [SummaryStatement(statement=s, source_quote=s) for s in _sentences(text)[:2]]
        return TriageDraft(
            matter_type=self.policy.area(area).label,
            practice_area=area,
            urgency=urgency,
            urgency_reasons=reasons,
            parties=parties,
            conflict_status=status,
            clarifying_questions=clarifying_questions(area, text, parties, enquiry.received_date),
            practitioner_summary=summary or [SummaryStatement(statement=text, source_quote=text)],
            holding_reply=self._reply(routing, area),
            routing=routing,
            routing_reason=f"Rules: area {area.value}, urgency {urgency.value}, conflict {status.value}.",
        )

    def triage(self, enquiry: Enquiry) -> TriageRecord:
        return assemble_record(
            enquiry=enquiry,
            arm="baseline",
            draft=self.draft(enquiry),
            failures=[],
            policy=self.policy,
            checker=self.checker,
        )

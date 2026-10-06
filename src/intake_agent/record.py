"""Turn a draft, from Claude or from the rules baseline, into the triage record the firm sees.

The same code-enforced rules apply to both arms:

1. The conflict check runs again in code over every party in the draft, the enquirer named in the
   form, and every name the deterministic extractor finds.  The draft cannot clear a conflict.
2. A potential conflict routes to the conflicts partner, replaces the holding reply with one that
   says nothing about the matter, and drops the clarifying questions.
3. Any failure, an unsupported summary statement, or a holding reply that trips the advice check
   routes to urgent human review with a neutral reply.
4. An urgent matter always goes to a person, and a consult is never booked without a clear check.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from intake_agent.audit import AuditEvent, Failure, ModelSummary, UsageSummary
from intake_agent.conflicts import (
    ConflictChecker,
    ConflictCheckResult,
    PartyCheck,
    PartyOutcome,
    PartyQuery,
    RegisterUnavailableError,
    compare_names,
    name_tokens,
)
from intake_agent.extract import candidate_names
from intake_agent.policy import IntakePolicy
from intake_agent.prompts import form_field_lines, neutralise_untrusted
from intake_agent.safety import (
    AdviceCheck,
    GroundingCheck,
    check_advice,
    check_grounding,
    conflict_reply,
    review_reply,
)
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

ReplySource = Literal["triage", "conflict_template", "review_template"]


class ConflictReport(BaseModel):
    """The conflict outcome.  `parties` holds full register detail for the conflicts partner only."""

    status: ConflictStatus
    reported_by_triage: ConflictStatus | None
    source: str
    checked_names: list[str]
    parties: list[PartyCheck] = Field(default_factory=list)


class RoutingReport(BaseModel):
    decision: Routing
    proposed_by_triage: Routing | None
    reason: str
    overrides: list[str] = Field(default_factory=list)


class Checks(BaseModel):
    advice: AdviceCheck | None
    grounding: GroundingCheck | None


class TriageRecord(BaseModel):
    enquiry_id: str
    arm: Literal["claude", "baseline"]
    received_date: date
    matter_type: str | None
    practice_area: PracticeArea | None
    urgency: Urgency | None
    urgency_reasons: list[str]
    conflict: ConflictReport
    parties: list[Party]
    clarifying_questions: list[str]
    practitioner_summary: list[SummaryStatement]
    holding_reply: str
    holding_reply_source: ReplySource
    routing: RoutingReport
    checks: Checks
    failures: list[Failure]
    model: ModelSummary | None = None
    usage: UsageSummary | None = None
    audit: list[AuditEvent] = Field(default_factory=list)


def conflict_queries(enquiry: Enquiry, draft: TriageDraft | None) -> list[PartyQuery]:
    """Every name the code checks: draft parties, the form's enquirer, then extracted names."""
    queries = (
        [PartyQuery(name=party.name, role=party.role, kind=party.kind) for party in draft.parties] if draft else []
    )
    if enquiry.fields.name:
        queries.append(PartyQuery(name=enquiry.fields.name, role=PartyRole.ENQUIRER, kind=PartyKind.PERSON))
    if enquiry.fields.organisation:
        queries.append(
            PartyQuery(name=enquiry.fields.organisation, role=PartyRole.ENQUIRER, kind=PartyKind.ORGANISATION)
        )
    known = [name_tokens(query.name) for query in queries]
    for name in candidate_names(enquiry.text):
        tokens = name_tokens(name)
        # A name that matches a party already listed is that party, with its known role.
        if any(
            compare_names(tokens, other, "person") or compare_names(tokens, other, "organisation") for other in known
        ):
            continue
        known.append(tokens)
        queries.append(PartyQuery(name=name, role=PartyRole.OTHER_PARTY, kind=PartyKind.UNKNOWN))
    return queries


def _final_conflict_check(
    checker: ConflictChecker,
    queries: list[PartyQuery],
    earlier: list[ConflictCheckResult],
    failures: list[Failure],
) -> ConflictCheckResult:
    try:
        result = checker.check(queries)
    except RegisterUnavailableError as exc:
        failures.append(Failure(kind="tool_error", detail=f"Final conflict check: {exc}"))
        return ConflictCheckResult(status=ConflictStatus.NOT_CHECKED, source=checker.source, parties=[])
    if result.status is not ConflictStatus.POTENTIAL_CONFLICT and any(
        item.status is ConflictStatus.POTENTIAL_CONFLICT for item in earlier
    ):
        # The triage step's own tool call flagged a name the final list no longer contains.
        flagged = [
            party for item in earlier for party in item.parties if party.outcome is PartyOutcome.POTENTIAL_CONFLICT
        ]
        return ConflictCheckResult(
            status=ConflictStatus.POTENTIAL_CONFLICT,
            source=result.source,
            parties=[*result.parties, *flagged],
        )
    return result


def _route(
    draft: TriageDraft | None,
    conflict: ConflictStatus,
    failures: list[Failure],
    advice: AdviceCheck | None,
) -> tuple[Routing, list[str]]:
    proposed = draft.routing if draft else None
    if conflict is ConflictStatus.POTENTIAL_CONFLICT:
        decision, why = Routing.CONFLICTS_PARTNER_REVIEW, "the conflict check found a potential conflict"
    elif failures:
        kinds = ", ".join(sorted({failure.kind for failure in failures}))
        decision, why = Routing.URGENT_HUMAN_REVIEW, f"the triage did not complete safely ({kinds})"
    elif advice is not None and advice.flagged:
        decision, why = Routing.URGENT_HUMAN_REVIEW, "the holding reply tripped the advice check"
    elif draft is not None and draft.urgency is Urgency.URGENT:
        decision, why = Routing.URGENT_HUMAN_REVIEW, "an urgent matter always goes to a person the same day"
    elif draft is not None and draft.routing is Routing.BOOK_CONSULT and conflict is not ConflictStatus.CLEAR:
        decision, why = (
            Routing.URGENT_HUMAN_REVIEW,
            "a consult cannot be booked before a clear conflict check",
        )
    else:
        assert draft is not None
        return draft.routing, []
    overrides = [] if proposed == decision else [f"Routed to {decision.value} instead of {proposed}: {why}."]
    return decision, overrides


def assemble_record(
    *,
    enquiry: Enquiry,
    arm: Literal["claude", "baseline"],
    draft: TriageDraft | None,
    failures: list[Failure],
    policy: IntakePolicy,
    checker: ConflictChecker,
    tool_conflict_results: list[ConflictCheckResult] | None = None,
    model: ModelSummary | None = None,
    usage: UsageSummary | None = None,
    audit: list[AuditEvent] | None = None,
) -> TriageRecord:
    failures = list(failures)
    queries = conflict_queries(enquiry, draft)
    conflict = _final_conflict_check(checker, queries, tool_conflict_results or [], failures)

    grounding = None
    advice = None
    if draft is not None:
        sources = [neutralise_untrusted(enquiry.text), *form_field_lines(enquiry)]
        grounding = check_grounding(draft.practitioner_summary, sources)
        if not grounding.passed:
            failures.append(
                Failure(
                    kind="invalid_output",
                    detail=f"{len(grounding.unsupported)} summary statement(s) not in the enquiry",
                )
            )
        advice = check_advice(draft.holding_reply)

    decision, overrides = _route(draft, conflict.status, failures, advice)
    reply_source: ReplySource
    if conflict.status is ConflictStatus.POTENTIAL_CONFLICT:
        reply, reply_source = conflict_reply(policy), "conflict_template"
    elif decision is Routing.URGENT_HUMAN_REVIEW and (failures or (advice is not None and advice.flagged)):
        reply, reply_source = review_reply(policy), "review_template"
    elif draft is not None:
        reply, reply_source = draft.holding_reply, "triage"
    else:
        reply, reply_source = review_reply(policy), "review_template"

    reason = overrides[0] if overrides else (draft.routing_reason if draft else "No triage draft was produced.")
    questions = (
        [] if conflict.status is ConflictStatus.POTENTIAL_CONFLICT or draft is None else draft.clarifying_questions
    )
    supported = (
        [s for s in draft.practitioner_summary if grounding is None or s.statement not in grounding.unsupported]
        if draft
        else []
    )
    return TriageRecord(
        enquiry_id=enquiry.enquiry_id,
        arm=arm,
        received_date=enquiry.received_date,
        matter_type=draft.matter_type if draft else None,
        practice_area=draft.practice_area if draft else None,
        urgency=draft.urgency if draft else None,
        urgency_reasons=draft.urgency_reasons if draft else [],
        conflict=ConflictReport(
            status=conflict.status,
            reported_by_triage=draft.conflict_status if draft else None,
            source=conflict.source,
            checked_names=[query.name for query in queries],
            parties=conflict.parties,
        ),
        parties=draft.parties if draft else [],
        clarifying_questions=questions,
        practitioner_summary=supported,
        holding_reply=reply,
        holding_reply_source=reply_source,
        routing=RoutingReport(
            decision=decision,
            proposed_by_triage=draft.routing if draft else None,
            reason=reason,
            overrides=overrides,
        ),
        checks=Checks(advice=advice, grounding=grounding),
        failures=failures,
        model=model,
        usage=usage,
        audit=audit or [],
    )

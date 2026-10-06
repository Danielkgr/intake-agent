from collections.abc import Sequence
from datetime import date
from typing import Any

from fakes import ENQUIRY, ScriptedAPI, api_message, draft, final_answer, tool_turn, tool_use
from intake_agent.agent import ClaudeTriageAgent
from intake_agent.audit import Failure
from intake_agent.conflicts import ConflictChecker, JsonConflictRegister, RegisterEntity
from intake_agent.policy import load_policy
from intake_agent.record import TriageRecord, assemble_record
from intake_agent.schema import ConflictStatus, Enquiry, EnquiryFields, Routing, TriageDraft

POLICY = load_policy()
CHECKER = ConflictChecker(JsonConflictRegister())

CONFLICT_ENQUIRY = Enquiry(
    enquiry_id="T-2",
    received_date=date(2026, 9, 29),
    fields=EnquiryFields(name="Nadia Ferreira-Holt"),
    text="Tamberlane Foods Pty Ltd dismissed me on 24 September 2026.  I was a forklift driver there.",
)


def _assemble(
    enquiry: Enquiry = ENQUIRY,
    failures: list[Failure] | None = None,
    checker: ConflictChecker = CHECKER,
    **overrides: Any,
) -> TriageRecord:
    return assemble_record(
        enquiry=enquiry,
        arm="claude",
        draft=TriageDraft.model_validate(draft(**overrides)),
        failures=failures or [],
        policy=POLICY,
        checker=checker,
    )


def test_a_clean_routine_draft_keeps_its_route_and_reply() -> None:
    record = _assemble(urgency="routine", routing="book_consult")
    assert record.routing.decision is Routing.BOOK_CONSULT
    assert record.routing.overrides == []
    assert record.holding_reply_source == "triage"
    assert record.conflict.status is ConflictStatus.CLEAR


def test_conflict_routes_to_the_conflicts_partner_even_when_the_model_says_otherwise() -> None:
    parties = [
        {"name": "Nadia Ferreira-Holt", "role": "enquirer", "kind": "person"},
        {"name": "Tamberlane Foods", "role": "opposing_party", "kind": "organisation"},
    ]
    summary = [{"statement": "Dismissed on 24 September 2026.", "source_quote": "dismissed me on 24 September 2026"}]
    api = ScriptedAPI(
        tool_turn(tool_use("toolu_1", "check_conflicts", {"parties": parties})),
        # The model ignores the tool's finding: it reports clear and books a consult.
        final_answer(
            parties=parties,
            practitioner_summary=summary,
            urgency="routine",
            conflict_status="clear",
            routing="book_consult",
        ),
    )
    record = ClaudeTriageAgent(client=api.client()).triage(CONFLICT_ENQUIRY)

    assert record.conflict.status is ConflictStatus.POTENTIAL_CONFLICT
    assert record.conflict.reported_by_triage is ConflictStatus.CLEAR
    assert record.routing.decision is Routing.CONFLICTS_PARTNER_REVIEW
    assert record.routing.proposed_by_triage is Routing.BOOK_CONSULT
    assert record.routing.overrides
    assert record.holding_reply_source == "conflict_template"
    assert "Tamberlane" not in record.holding_reply
    assert record.clarifying_questions == []
    assert record.model is not None and record.usage is not None and record.audit


def test_conflict_is_caught_even_when_the_draft_leaves_the_party_out() -> None:
    summary = [{"statement": "Dismissed on 24 September 2026.", "source_quote": "dismissed me on 24 September 2026"}]
    record = _assemble(
        enquiry=CONFLICT_ENQUIRY,
        parties=[{"name": "Nadia Ferreira-Holt", "role": "enquirer", "kind": "person"}],
        practitioner_summary=summary,
    )
    assert "Tamberlane Foods Pty Ltd" in record.conflict.checked_names
    assert record.routing.decision is Routing.CONFLICTS_PARTNER_REVIEW


def test_refusal_routes_to_urgent_human_review_with_a_neutral_reply() -> None:
    refusal = api_message([], "refusal", stop_details={"type": "refusal", "category": None, "explanation": None})
    record = ClaudeTriageAgent(client=ScriptedAPI(refusal).client()).triage(ENQUIRY)
    assert record.routing.decision is Routing.URGENT_HUMAN_REVIEW
    assert record.practice_area is None
    assert record.holding_reply_source == "review_template"
    assert [failure.kind for failure in record.failures] == ["refusal"]


def test_max_tokens_routes_to_urgent_human_review() -> None:
    truncated = api_message([], "max_tokens")
    record = ClaudeTriageAgent(client=ScriptedAPI(truncated).client()).triage(ENQUIRY)
    assert record.routing.decision is Routing.URGENT_HUMAN_REVIEW


def test_advice_in_the_holding_reply_routes_to_review_and_is_not_sent() -> None:
    advice = "You should lodge an unfair dismissal claim within 21 days under the Fair Work Act 2009."
    record = _assemble(urgency="routine", routing="book_consult", holding_reply=advice)
    assert record.checks.advice is not None and record.checks.advice.flagged
    assert record.routing.decision is Routing.URGENT_HUMAN_REVIEW
    assert record.holding_reply_source == "review_template"
    assert record.holding_reply != advice
    assert record.checks.advice.checked_text == advice


def test_unsupported_summary_statement_is_dropped_and_routes_to_review() -> None:
    summary = [
        {"statement": "Dismissed on 24 September 2026.", "source_quote": "on 24 September 2026"},
        {"statement": "Earns $95,000 a year.", "source_quote": "I earn $95,000 a year"},
    ]
    record = _assemble(urgency="routine", routing="book_consult", practitioner_summary=summary)
    assert record.checks.grounding is not None
    assert record.checks.grounding.unsupported == ["Earns $95,000 a year."]
    assert [s.statement for s in record.practitioner_summary] == ["Dismissed on 24 September 2026."]
    assert record.routing.decision is Routing.URGENT_HUMAN_REVIEW


def test_urgent_matter_is_never_booked_as_a_routine_consult() -> None:
    record = _assemble(urgency="urgent", routing="book_consult")
    assert record.routing.decision is Routing.URGENT_HUMAN_REVIEW
    assert record.routing.overrides


def test_returning_client_who_signs_the_enquiry_is_not_a_conflict() -> None:
    enquiry = Enquiry(
        received_date=date(2026, 9, 29),
        fields=EnquiryFields(name="Marguerite Okonkwo-Bell"),
        text="I would like to update my will.\nRegards,\nMarguerite Okonkwo-Bell",
    )
    record = _assemble(
        enquiry=enquiry,
        parties=[{"name": "Marguerite Okonkwo-Bell", "role": "enquirer", "kind": "person"}],
        practitioner_summary=[{"statement": "Wants to update a will.", "source_quote": "update my will"}],
        urgency="routine",
        routing="book_consult",
    )
    assert record.conflict.status is ConflictStatus.CLEAR
    assert record.routing.decision is Routing.BOOK_CONSULT


def test_register_outage_fails_closed() -> None:
    class BrokenRegister:
        source = "broken"

        def candidates(self, name: str) -> Sequence[RegisterEntity]:
            raise ConnectionError("timed out")

    record = _assemble(urgency="routine", routing="book_consult", checker=ConflictChecker(BrokenRegister()))
    assert record.conflict.status is ConflictStatus.NOT_CHECKED
    assert record.routing.decision is Routing.URGENT_HUMAN_REVIEW
    assert record.failures[0].kind == "tool_error"

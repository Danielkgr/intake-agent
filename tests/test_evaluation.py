import json
from pathlib import Path

import pytest

from fakes import ScriptedAPI, api_message
from intake_agent.cli import main
from intake_agent.conflicts import ConflictChecker, JsonConflictRegister
from intake_agent.evaluation import EvalCase, dataset_sha256, load_cases, score
from intake_agent.policy import load_policy
from intake_agent.record import TriageRecord, assemble_record
from intake_agent.schema import ConflictStatus, Routing, TriageDraft, Urgency

CASES = load_cases()


def test_dataset_has_between_20_and_30_uniquely_named_cases() -> None:
    ids = [case.enquiry.enquiry_id for case in CASES]
    assert 20 <= len(ids) <= 30
    assert len(set(ids)) == len(ids)
    assert len(dataset_sha256()) == 64


def test_dataset_covers_every_required_kind_of_case() -> None:
    areas = {case.expected.practice_area for case in CASES}
    tags = {tag for case in CASES for tag in case.tags}
    assert len(areas) == 8
    assert {"conflict", "injection", "advice_request", "missing_info"} <= tags
    assert any(case.expected.urgency is Urgency.URGENT for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.enquiry.enquiry_id)
def test_labels_follow_the_routing_rules(case: EvalCase) -> None:
    expected = case.expected
    if expected.conflict_status is ConflictStatus.POTENTIAL_CONFLICT:
        assert expected.routing is Routing.CONFLICTS_PARTNER_REVIEW
    elif expected.urgency is Urgency.URGENT:
        assert expected.routing is Routing.URGENT_HUMAN_REVIEW
    else:
        assert expected.routing in {Routing.BOOK_CONSULT, Routing.DECLINE_AND_REFER}


def _record_for(case: EvalCase, **overrides: object) -> TriageRecord:
    draft = {
        "matter_type": "Test",
        "practice_area": case.expected.practice_area.value,
        "urgency": case.expected.urgency.value,
        "urgency_reasons": [],
        "parties": [],
        "conflict_status": case.expected.conflict_status.value,
        "clarifying_questions": [],
        "practitioner_summary": [{"statement": "Text.", "source_quote": case.enquiry.text[:20]}],
        "holding_reply": "Thank you for contacting us.  We will be in touch within one business day.",
        "routing": case.expected.routing.value,
        "routing_reason": "Test.",
    }
    draft.update(overrides)
    return assemble_record(
        enquiry=case.enquiry,
        arm="baseline",
        draft=TriageDraft.model_validate(draft),
        failures=[],
        policy=load_policy(),
        checker=ConflictChecker(JsonConflictRegister()),
    )


def test_scorer_counts_exact_matches_conflict_recall_and_advice_flags() -> None:
    by_id = {case.enquiry.enquiry_id: case for case in CASES}
    cases = [by_id["EMP-02"], by_id["EMP-03"], by_id["OOS-02"]]
    records = [
        _record_for(cases[0], holding_reply="You should keep your payslips."),
        _record_for(cases[1], parties=[]),
        _record_for(cases[2], practice_area="commercial_disputes"),
    ]
    summary, results = score(cases, records, arm="baseline")

    assert summary.cases == 3
    # The advice check sends EMP-02 to urgent review, so its routing misses.
    assert summary.fields["routing"].correct == 2
    assert summary.fields["practice_area"].correct == 2
    assert summary.advice_flags == 1
    assert summary.advice_flagged_cases == ["EMP-02"]
    # EMP-03's draft names no parties, but the code's own check still finds the conflict.
    assert summary.conflict.expected_conflicts == 1
    assert summary.conflict.recall == 1.0
    assert summary.conflict.missed == []
    assert [result.all_correct for result in results] == [False, True, False]


def test_a_missed_conflict_is_reported_by_name() -> None:
    case = next(case for case in CASES if case.enquiry.enquiry_id == "EMP-03")
    record = _record_for(case).model_copy(
        update={"conflict": _record_for(case).conflict.model_copy(update={"status": ConflictStatus.CLEAR})}
    )
    summary, _ = score([case], [record], arm="baseline")
    assert summary.conflict.recall == 0.0
    assert summary.conflict.missed == ["EMP-03"]


def test_claude_eval_runs_without_fallbacks_and_writes_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    refusal = api_message([], "refusal", stop_details={"type": "refusal", "category": None, "explanation": None})
    api = ScriptedAPI(refusal, refusal)
    client = api.client()
    monkeypatch.setattr("intake_agent.agent.anthropic.Anthropic", lambda **_: client)

    code = main(["eval", "--arm", "claude", "--limit", "2", "--out", str(tmp_path)])

    assert code == 0
    assert all("fallbacks" not in request for request in api.requests)
    payload = json.loads((tmp_path / "results.json").read_text())
    assert payload["summary"]["failures_by_kind"] == {"refusal": 2}
    assert payload["provenance"]["data_version"] == "intake-eval-v1"
    assert payload["provenance"]["settings"]["fallbacks"] is False
    assert len((tmp_path / "records.jsonl").read_text().splitlines()) == 2
    assert "Conflict recall" in capsys.readouterr().out

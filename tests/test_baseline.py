from datetime import date

from intake_agent.baseline import RulesBaseline, assess_urgency, classify_area, dated_events, find_parties
from intake_agent.evaluation import load_cases
from intake_agent.schema import Enquiry, PartyRole, PracticeArea, Routing, Urgency

RECEIVED = date(2026, 3, 10)


def test_keywords_pick_the_practice_area() -> None:
    assert classify_area("I was charged with drink driving last month.") is PracticeArea.CRIMINAL
    assert classify_area("My landlord wants me out before the lease ends.") is PracticeArea.PROPERTY_CONVEYANCING
    assert classify_area("I need help with my visa.") is PracticeArea.OUT_OF_SCOPE
    assert classify_area("Hello there.") is PracticeArea.OUT_OF_SCOPE


def test_dates_without_a_year_resolve_to_the_nearest_year() -> None:
    events = dated_events("It happened on 2 March.  The hearing is tomorrow.", RECEIVED)
    assert [event.when for event in events] == [date(2026, 3, 2), date(2026, 3, 11)]


def test_recent_dismissal_is_urgent_and_an_old_one_is_time_sensitive() -> None:
    recent = assess_urgency(PracticeArea.EMPLOYMENT, "I was dismissed on 1 March 2026.", RECEIVED)
    older = assess_urgency(PracticeArea.EMPLOYMENT, "I was dismissed on 5 January 2026.", RECEIVED)
    assert recent[0] is Urgency.URGENT
    assert older[0] is Urgency.TIME_SENSITIVE


def test_court_date_bands_follow_the_policy() -> None:
    soon = assess_urgency(PracticeArea.CRIMINAL, "I have to appear in court on 14 March 2026.", RECEIVED)
    later = assess_urgency(PracticeArea.CRIMINAL, "I have to appear in court on 2 April 2026.", RECEIVED)
    far = assess_urgency(PracticeArea.CRIMINAL, "I have to appear in court on 2 June 2026.", RECEIVED)
    assert (soon[0], later[0], far[0]) == (Urgency.URGENT, Urgency.TIME_SENSITIVE, Urgency.ROUTINE)


def test_an_email_sign_off_names_the_enquirer() -> None:
    enquiry = Enquiry(received_date=RECEIVED, text="Please help with my lease.\n\nKind regards,\nOrla Penhallow")
    parties = find_parties(enquiry)
    assert parties[0].name == "Orla Penhallow"
    assert parties[0].role is PartyRole.ENQUIRER


def test_out_of_scope_enquiry_is_declined_with_a_template_reply() -> None:
    enquiry = Enquiry(received_date=RECEIVED, text="I want to register a trade mark for my bakery.")
    record = RulesBaseline().triage(enquiry)
    assert record.routing.decision is Routing.DECLINE_AND_REFER
    assert record.checks.advice is not None and not record.checks.advice.flagged
    assert record.checks.grounding is not None and record.checks.grounding.passed


def test_baseline_is_deterministic_over_the_evaluation_set() -> None:
    baseline = RulesBaseline()
    cases = load_cases()
    first = [baseline.triage(case.enquiry).model_dump_json() for case in cases]
    second = [baseline.triage(case.enquiry).model_dump_json() for case in cases]
    assert first == second

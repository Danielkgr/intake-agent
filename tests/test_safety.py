import pytest

from intake_agent.extract import candidate_names
from intake_agent.policy import load_policy
from intake_agent.safety import check_advice, check_grounding, conflict_reply, review_reply
from intake_agent.schema import SummaryStatement


@pytest.mark.parametrize(
    "reply",
    [
        "You should lodge an application with the Fair Work Commission straight away.",
        "We recommend that you keep every payslip.",
        "Based on what you describe, you have a strong case.",
        "You are entitled to notice pay.",
        "Your dismissal was unlawful.",
        "You will win this.",
        "Under the Act your employer owes you redundancy pay.",
        "Please be aware of s 394 and the time limit that applies.",
        "The Fair Work Act 2009 gives you 21 days.",
        "Don't sign anything your employer sends you.",
        "Our advice is to wait.",
    ],
)
def test_advice_like_replies_are_flagged(reply: str) -> None:
    assert check_advice(reply).flagged


@pytest.mark.parametrize(
    "reply",
    [
        "Thank you for contacting Quollridge Lawyers.  We have received your enquiry and will contact you "
        "within one business day.",
        "Thank you for your message.  Quollridge Lawyers does not act in immigration matters, so please look "
        "for a lawyer who practises in that area.",
        "It's 3 days until your hearing, so please telephone our office as well.",
        "We need a few details before we can book a time to talk.",
    ],
)
def test_ordinary_holding_replies_pass(reply: str) -> None:
    assert not check_advice(reply).flagged


def test_the_firms_own_templates_pass_the_advice_check() -> None:
    policy = load_policy()
    assert not check_advice(conflict_reply(policy)).flagged
    assert not check_advice(review_reply(policy)).flagged


def test_grounding_accepts_exact_quotes_despite_quote_marks_and_spacing() -> None:
    source = "My boss said \u2018you\u2019re finished\u2019   on Friday."
    statements = [
        SummaryStatement(statement="Told he was finished.", source_quote="said 'you're finished' on Friday")
    ]
    assert check_grounding(statements, [source]).passed


def test_grounding_rejects_a_quote_the_enquiry_does_not_contain() -> None:
    statements = [
        SummaryStatement(statement="Dismissed on Friday.", source_quote="dismissed on Friday"),
        SummaryStatement(statement="Earns $90,000.", source_quote="I earn $90,000"),
        SummaryStatement(statement="Empty quote.", source_quote="   "),
    ]
    result = check_grounding(statements, ["I was dismissed on Friday."])
    assert not result.passed
    assert result.unsupported == ["Earns $90,000.", "Empty quote."]


def test_extractor_finds_people_and_businesses_but_not_greetings_or_dates() -> None:
    text = (
        "Dear Sir,\nOn Monday 3 August my landlord Velloran Property Group served a notice.  "
        "Mr Pemberton-Hale and Brindlemoor Freight & Logistics Pty Ltd are involved."
        "\nKind regards,\nPetra A. Lindqvist"
    )
    names = candidate_names(text)
    assert names == [
        "Pemberton-Hale",
        "Velloran Property Group",
        "Brindlemoor Freight & Logistics Pty Ltd",
        "Petra A. Lindqvist",
    ]

from intake_agent.policy import load_policy
from intake_agent.schema import PracticeArea


def test_policy_covers_every_practice_area_and_is_marked_fictional() -> None:
    policy = load_policy()
    assert set(policy.areas) == set(PracticeArea)
    assert policy.firm.fictional is True


def test_area_rules_for_the_tool_include_required_information() -> None:
    rules = load_policy().area_for_model(PracticeArea.EMPLOYMENT)
    assert rules["firm_handles"] is True
    assert any("21 days" in trigger for trigger in rules["urgency_triggers"])
    assert rules["required_information"]


def test_out_of_scope_rules_list_the_areas_the_firm_does_not_practise() -> None:
    rules = load_policy().area_for_model(PracticeArea.OUT_OF_SCOPE)
    assert rules["firm_handles"] is False
    assert "Immigration and visas" in rules["out_of_scope_areas"]


def test_prompt_summary_is_stable_and_names_every_route() -> None:
    first, second = load_policy().summary_for_prompt(), load_policy().summary_for_prompt()
    assert first == second
    for route in ("book_consult", "conflicts_partner_review", "decline_and_refer", "urgent_human_review"):
        assert route in first

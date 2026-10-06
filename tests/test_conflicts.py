import json
from pathlib import Path

import pytest

from intake_agent.conflicts import (
    ConflictChecker,
    JsonConflictRegister,
    MatchType,
    PartyOutcome,
    PartyQuery,
    RegisterEntity,
    RegisterUnavailableError,
    compare_names,
    name_tokens,
    normalise_name,
)
from intake_agent.schema import ConflictStatus, PartyKind, PartyRole


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tamberlane Foods Pty. Ltd.", "tamberlane foods"),
        ("TAMBERLANE FOODS PTY LTD", "tamberlane foods"),
        ("Tamberlane Foods Proprietary Limited", "tamberlane foods"),
        ("The Brindlemoor Freight & Logistics Pty Ltd", "brindlemoor freight and logistics"),
        ("Dunmore Agistment Co.", "dunmore agistment"),
        ("Smith & Co", "smith"),
        ("Ms Zo\u00eb O\u2019Rourke-Vance", "zoe orourke vance"),
        ("ASHDOWN-PRYCE, Corin", "ashdown pryce corin"),
        ("  ", ""),
    ],
)
def test_normalisation_ignores_case_punctuation_titles_and_company_suffixes(raw: str, expected: str) -> None:
    assert normalise_name(raw) == expected


def _compare(query: str, candidate: str, kind: str = "person") -> MatchType | None:
    assert kind in ("person", "organisation")
    return compare_names(name_tokens(query), name_tokens(candidate), kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("query", "candidate", "kind", "expected"),
    [
        ("tamberlane foods", "Tamberlane Foods Pty. Ltd.", "organisation", MatchType.EXACT),
        ("Corin Ashdown-Pryce", "ASHDOWN-PRYCE, Corin", "person", MatchType.REORDERED),
        ("Petra Lindqvist", "Petra A. Lindqvist", "person", MatchType.INITIALS),
        ("P. Lindqvist", "Petra Lindqvist", "person", MatchType.INITIALS),
        ("Corvid Analytcs", "Corvid Analytics Pty Ltd", "organisation", MatchType.NEAR_SPELLING),
        ("Marguerite Okonkwo-Bel", "Marguerite Okonkwo-Bell", "person", None),
        ("Margeurite Okonkwo-Bell", "Marguerite Okonkwo-Bell", "person", MatchType.NEAR_SPELLING),
        ("Velloran Property Group", "Velloran Properties Pty Ltd", "organisation", MatchType.PARTIAL),
        ("Kestrel Ridge", "Kestrel Ridge Mining Ltd", "organisation", MatchType.PARTIAL),
        ("Mr Pemberton-Hale", "Arthur Pemberton-Hale", "person", MatchType.PARTIAL),
        ("Pemberton", "Arthur Pemberton", "person", MatchType.PARTIAL),
    ],
)
def test_name_variants_that_should_be_flagged(
    query: str, candidate: str, kind: str, expected: MatchType | None
) -> None:
    assert _compare(query, candidate, kind) == expected


@pytest.mark.parametrize(
    ("query", "candidate", "kind"),
    [
        ("Glenys Pemberton-Hale", "Arthur Pemberton-Hale", "person"),
        ("Arthur Penrose", "Arthur Pemberton", "person"),
        ("Holdings Group", "Velloran Holdings Group Pty Ltd", "organisation"),
        ("Australian Services", "Kestrel Australian Services", "organisation"),
        ("Ann Lee", "Ann Leigh", "person"),
    ],
)
def test_different_parties_are_not_matched(query: str, candidate: str, kind: str) -> None:
    assert _compare(query, candidate, kind) is None


@pytest.fixture
def checker() -> ConflictChecker:
    return ConflictChecker(JsonConflictRegister())


def test_opposing_party_who_is_a_current_client_is_a_potential_conflict(checker: ConflictChecker) -> None:
    result = checker.check(
        [
            PartyQuery(name="Nadia Ferreira-Holt", role=PartyRole.ENQUIRER, kind=PartyKind.PERSON),
            PartyQuery(name="Tamberlane Foods", role=PartyRole.OPPOSING_PARTY, kind=PartyKind.ORGANISATION),
        ]
    )
    assert result.status is ConflictStatus.POTENTIAL_CONFLICT
    flagged = [party for party in result.parties if party.outcome is PartyOutcome.POTENTIAL_CONFLICT]
    assert [party.name for party in flagged] == ["Tamberlane Foods"]
    assert flagged[0].matches[0].relationships[0].matter_id == "M-2024-117"


def test_former_client_on_the_other_side_is_a_potential_conflict(checker: ConflictChecker) -> None:
    result = checker.check([PartyQuery(name="Corvid Analytics", role=PartyRole.OPPOSING_PARTY)])
    assert result.status is ConflictStatus.POTENTIAL_CONFLICT


def test_enquirer_who_is_an_adverse_party_is_a_potential_conflict(checker: ConflictChecker) -> None:
    result = checker.check([PartyQuery(name="Petra Lindqvist", role=PartyRole.ENQUIRER)])
    assert result.status is ConflictStatus.POTENTIAL_CONFLICT


def test_returning_client_and_known_adverse_party_are_clear(checker: ConflictChecker) -> None:
    result = checker.check(
        [
            PartyQuery(name="Marguerite Okonkwo-Bell", role=PartyRole.ENQUIRER),
            PartyQuery(name="Halverson Joinery Pty Ltd", role=PartyRole.OPPOSING_PARTY),
        ]
    )
    assert result.status is ConflictStatus.CLEAR
    assert [party.outcome for party in result.parties] == [
        PartyOutcome.EXISTING_CLIENT,
        PartyOutcome.KNOWN_ADVERSE_PARTY,
    ]


def test_related_party_such_as_a_director_is_a_potential_conflict(checker: ConflictChecker) -> None:
    result = checker.check([PartyQuery(name="Ilse Varga-Thornbury", role=PartyRole.OTHER_PARTY)])
    assert result.status is ConflictStatus.POTENTIAL_CONFLICT


def test_no_names_means_not_checked(checker: ConflictChecker) -> None:
    assert checker.check([]).status is ConflictStatus.NOT_CHECKED
    assert checker.check([PartyQuery(name=" - ", role=PartyRole.OTHER_PARTY)]).status is ConflictStatus.NOT_CHECKED


def test_model_view_withholds_matter_details_and_register_roles(checker: ConflictChecker) -> None:
    view = checker.check(
        [
            PartyQuery(name="Marguerite Okonkwo-Bell", role=PartyRole.ENQUIRER),
            PartyQuery(name="Tamberlane Foods", role=PartyRole.OPPOSING_PARTY),
            PartyQuery(name="Halverson Joinery", role=PartyRole.OTHER_PARTY),
        ]
    ).for_model()
    text = json.dumps(view)
    assert view["status"] == "potential_conflict"
    assert [party["outcome"] for party in view["parties"]] == ["no_conflict", "potential_conflict", "no_conflict"]
    assert "M-2024-117" not in text
    assert "adverse" not in text
    assert "client" not in text.replace("conflicts_partner_review", "")


def test_register_failure_raises_a_typed_error() -> None:
    class BrokenRegister:
        source = "broken"

        def candidates(self, name: str) -> list[RegisterEntity]:
            raise ConnectionError("practice management system timed out")

    with pytest.raises(RegisterUnavailableError):
        ConflictChecker(BrokenRegister()).check([PartyQuery(name="Anyone Atall", role=PartyRole.ENQUIRER)])


def test_register_rejects_a_matter_that_names_an_unknown_entity(tmp_path: Path) -> None:
    path = tmp_path / "register.json"
    path.write_text(
        json.dumps(
            {
                "register_version": "test",
                "entities": [],
                "matters": [{"matter_id": "M-1", "status": "open", "practice_area": "family", "client_ids": ["E99"]}],
            }
        )
    )
    with pytest.raises(ValueError, match="E99"):
        JsonConflictRegister(path)

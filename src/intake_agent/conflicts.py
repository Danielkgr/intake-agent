"""Conflict checking against a register of clients, matters, and adverse parties.

The register sits behind a small protocol, so a practice management system can replace the JSON
file without touching the agent.  The checker in this module, not the model, decides what counts
as a match and whether a match is a potential conflict.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from intake_agent.schema import ConflictStatus, PartyKind, PartyRole

# Titles dropped from the front of a person's name.
_TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "dame"})

# Company designators dropped from the end of an organisation's name, longest first.
_COMPANY_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("proprietary", "limited"),
    ("proprietary", "ltd"),
    ("pty", "limited"),
    ("pty", "ltd"),
    ("p", "l"),
    ("pty",),
    ("ltd",),
    ("limited",),
    ("incorporated",),
    ("inc",),
    ("llc",),
    ("plc",),
    ("corporation",),
    ("corp",),
    ("company",),
    ("co",),
    ("nl",),
)

# Words too common in business names to identify an organisation on their own.
_GENERIC_TOKENS = frozenset(
    {
        "and",
        "of",
        "the",
        "group",
        "holdings",
        "services",
        "australia",
        "australian",
        "trading",
        "enterprises",
        "investments",
        "trust",
        "family",
        "partners",
        "solutions",
        "industries",
        "international",
        "management",
        "consulting",
        "properties",
        "property",
        "developments",
    }
)

_APOSTROPHES = re.compile("['`\u2018\u2019\u02bc]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def name_tokens(name: str) -> tuple[str, ...]:
    """Normalise a name into comparable tokens.

    Case, accents, punctuation, a leading "the", personal titles, and trailing company
    designators such as "Pty Ltd" are removed, and "&" is read as "and".
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ")
    text = _APOSTROPHES.sub("", text)
    tokens = [token for token in _NON_ALNUM.sub(" ", text).split() if token]
    while tokens and (tokens[0] == "the" or tokens[0] in _TITLES):
        tokens.pop(0)
    stripped = True
    while stripped and tokens:
        stripped = False
        for suffix in _COMPANY_SUFFIXES:
            if len(tokens) > len(suffix) and tuple(tokens[-len(suffix) :]) == suffix:
                del tokens[-len(suffix) :]
                stripped = True
                break
        if len(tokens) > 1 and tokens[-1] == "and":
            tokens.pop()
            stripped = True
    return tuple(tokens)


def normalise_name(name: str) -> str:
    return " ".join(name_tokens(name))


def _within_one_edit(a: str, b: str) -> bool:
    """True when a and b differ by one insertion, deletion, substitution, or adjacent swap."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
        if len(diffs) == 1:
            return True
        return (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        )
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))


def _same_or_initial(a: str, b: str) -> bool:
    if a == b:
        return True
    return (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def _distinctive(tokens: Iterable[str]) -> list[str]:
    return [token for token in tokens if token not in _GENERIC_TOKENS and len(token) >= 3]


class MatchType(StrEnum):
    EXACT = "exact"
    REORDERED = "reordered"
    INITIALS = "initials"
    NEAR_SPELLING = "near_spelling"
    PARTIAL = "partial"


def compare_names(
    query: Sequence[str], candidate: Sequence[str], candidate_kind: Literal["person", "organisation"]
) -> MatchType | None:
    """Decide whether two normalised names refer to the same party closely enough to flag.

    The rules lean towards flagging, because a missed conflict costs far more than a partner's
    minute spent clearing a false one.
    """
    if not query or not candidate:
        return None
    if tuple(query) == tuple(candidate):
        return MatchType.EXACT
    if sorted(query) == sorted(candidate):
        return MatchType.REORDERED

    # A given name written as an initial, or a middle name present on one side only.
    if (
        len(query) >= 2
        and len(candidate) >= 2
        and query[-1] == candidate[-1]
        and _same_or_initial(query[0], candidate[0])
    ):
        q_mid, c_mid = query[1:-1], candidate[1:-1]
        if (
            not q_mid
            or not c_mid
            or (
                len(q_mid) == len(c_mid)
                and all(_same_or_initial(a, b) for a, b in zip(q_mid, c_mid, strict=True))
            )
        ):
            return MatchType.INITIALS

    # One misspelt word of five or more letters.
    if len(query) == len(candidate):
        differing = [(a, b) for a, b in zip(query, candidate, strict=True) if a != b]
        if len(differing) == 1:
            a, b = differing[0]
            if min(len(a), len(b)) >= 5 and _within_one_edit(a, b):
                return MatchType.NEAR_SPELLING

    # The shorter name is contained in the longer one.
    shorter, longer = (query, candidate) if len(query) <= len(candidate) else (candidate, query)
    shorter_distinctive = _distinctive(shorter)
    if len(shorter_distinctive) >= 2 and set(shorter_distinctive) <= set(longer):
        return MatchType.PARTIAL
    # A surname on its own, such as "Mr Pemberton".
    if candidate_kind == "person" and len(query) == 1 and len(query[0]) >= 4 and query[0] == candidate[-1]:
        return MatchType.PARTIAL
    # A business named by its distinctive first word, such as a trading name against a legal name.
    if candidate_kind == "organisation":
        query_distinctive, candidate_distinctive = _distinctive(query), _distinctive(candidate)
        if query_distinctive and candidate_distinctive and query_distinctive[0] == candidate_distinctive[0]:
            return MatchType.PARTIAL if len(query_distinctive[0]) >= 4 else None
    return None


class RelationshipRole(StrEnum):
    CLIENT = "client"
    ADVERSE_PARTY = "adverse_party"
    RELATED_PARTY = "related_party"


class Relationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: str
    matter_status: Literal["open", "closed"]
    practice_area: str
    role: RelationshipRole


class RegisterEntity(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_id: str
    name: str
    kind: Literal["person", "organisation"]
    aliases: tuple[str, ...] = ()
    relationships: tuple[Relationship, ...] = ()


class ConflictRegister(Protocol):
    """Where the firm's conflict data comes from.

    `candidates` must return every entity the source system could consider a match for the
    name, and may return more.  The checker applies the matching rules.  An adapter for a
    practice management system must search at least as broadly as those rules: surname alone,
    a distinctive word of a business name, and a one-letter misspelling.
    """

    @property
    def source(self) -> str: ...

    def candidates(self, name: str) -> Sequence[RegisterEntity]: ...


class RegisterUnavailableError(RuntimeError):
    """The conflict register could not be searched."""


def _load_register_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        text = (
            resources.files("intake_agent")
            .joinpath("data/conflict_register.json")
            .read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


class JsonConflictRegister:
    """A mock register of fictional clients, matters, and adverse parties, read from JSON.

    It returns every entity as a candidate, so the checker sees the whole register.
    """

    def __init__(self, path: Path | None = None) -> None:
        data = _load_register_json(path)
        self._version = str(data["register_version"])
        raw_entities = {item["entity_id"]: item for item in data["entities"]}
        relationships: dict[str, list[Relationship]] = {entity_id: [] for entity_id in raw_entities}
        for matter in data["matters"]:
            for key, role in (
                ("client_ids", RelationshipRole.CLIENT),
                ("adverse_party_ids", RelationshipRole.ADVERSE_PARTY),
                ("related_party_ids", RelationshipRole.RELATED_PARTY),
            ):
                for entity_id in matter.get(key, []):
                    if entity_id not in raw_entities:
                        raise ValueError(f"Matter {matter['matter_id']} names unknown entity {entity_id}")
                    relationships[entity_id].append(
                        Relationship(
                            matter_id=matter["matter_id"],
                            matter_status=matter["status"],
                            practice_area=matter["practice_area"],
                            role=role,
                        )
                    )
        self._entities = tuple(
            RegisterEntity(
                entity_id=entity_id,
                name=item["name"],
                kind=item["kind"],
                aliases=tuple(item.get("aliases", [])),
                relationships=tuple(relationships[entity_id]),
            )
            for entity_id, item in raw_entities.items()
        )

    @property
    def source(self) -> str:
        return f"mock JSON register {self._version} (fictional)"

    @property
    def entities(self) -> tuple[RegisterEntity, ...]:
        return self._entities

    def candidates(self, name: str) -> Sequence[RegisterEntity]:
        return self._entities


class PartyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: PartyRole
    kind: PartyKind = PartyKind.UNKNOWN


class PartyOutcome(StrEnum):
    NO_MATCH = "no_match"
    EXISTING_CLIENT = "existing_client"
    KNOWN_ADVERSE_PARTY = "known_adverse_party"
    POTENTIAL_CONFLICT = "potential_conflict"


class RegisterMatch(BaseModel):
    entity_id: str
    entity_name: str
    matched_name: str
    match_type: MatchType
    relationships: list[Relationship]


class PartyCheck(BaseModel):
    name: str
    role: PartyRole
    normalised: str
    outcome: PartyOutcome
    matches: list[RegisterMatch] = Field(default_factory=list)


class ConflictCheckResult(BaseModel):
    status: ConflictStatus
    source: str
    parties: list[PartyCheck]

    def for_model(self) -> dict[str, Any]:
        """The result as the model sees it.

        Matter numbers and other clients' roles stay out of the model's context.  The model
        needs only the outcome for each name.
        """
        return {
            "status": self.status.value,
            "parties": [
                {
                    "name": party.name,
                    "role": party.role.value,
                    "outcome": party.outcome.value,
                    "match_types": sorted({match.match_type.value for match in party.matches}),
                }
                for party in self.parties
            ],
            "note": (
                "Matter details are withheld.  If status is potential_conflict, route to "
                "conflicts_partner_review, ask no clarifying questions, and keep the holding reply "
                "free of any discussion of the matter."
            ),
        }


def _decide(role: PartyRole, matches: Sequence[RegisterMatch]) -> PartyOutcome:
    if not matches:
        return PartyOutcome.NO_MATCH
    roles = {relationship.role for match in matches for relationship in match.relationships}
    if role is PartyRole.ENQUIRER and roles == {RelationshipRole.CLIENT}:
        return PartyOutcome.EXISTING_CLIENT
    if role is not PartyRole.ENQUIRER and roles == {RelationshipRole.ADVERSE_PARTY}:
        return PartyOutcome.KNOWN_ADVERSE_PARTY
    return PartyOutcome.POTENTIAL_CONFLICT


class ConflictChecker:
    """Applies the matching rules and the conflict decision table to a register.

    | Party searched | Register shows | Outcome |
    |---|---|---|
    | Enquirer | Client only | Existing client, no conflict |
    | Enquirer | Adverse or related party | Potential conflict |
    | Any other party | Adverse party only | Known adverse party, no conflict |
    | Any other party | Client or related party | Potential conflict |
    """

    def __init__(self, register: ConflictRegister) -> None:
        self._register = register

    @property
    def source(self) -> str:
        return self._register.source

    def check(self, parties: Sequence[PartyQuery]) -> ConflictCheckResult:
        checks: list[PartyCheck] = []
        seen: set[tuple[str, PartyRole]] = set()
        for party in parties:
            tokens = name_tokens(party.name)
            if not tokens or (" ".join(tokens), party.role) in seen:
                continue
            seen.add((" ".join(tokens), party.role))
            try:
                candidates = self._register.candidates(party.name)
            except Exception as exc:
                raise RegisterUnavailableError(f"Conflict register search failed: {exc}") from exc
            matches: list[RegisterMatch] = []
            for entity in candidates:
                for candidate_name in (entity.name, *entity.aliases):
                    match_type = compare_names(tokens, name_tokens(candidate_name), entity.kind)
                    if match_type is not None:
                        matches.append(
                            RegisterMatch(
                                entity_id=entity.entity_id,
                                entity_name=entity.name,
                                matched_name=candidate_name,
                                match_type=match_type,
                                relationships=list(entity.relationships),
                            )
                        )
                        break
            checks.append(
                PartyCheck(
                    name=party.name,
                    role=party.role,
                    normalised=" ".join(tokens),
                    outcome=_decide(party.role, matches),
                    matches=matches,
                )
            )
        if not checks:
            status = ConflictStatus.NOT_CHECKED
        elif any(check.outcome is PartyOutcome.POTENTIAL_CONFLICT for check in checks):
            status = ConflictStatus.POTENTIAL_CONFLICT
        else:
            status = ConflictStatus.CLEAR
        return ConflictCheckResult(status=status, source=self.source, parties=checks)

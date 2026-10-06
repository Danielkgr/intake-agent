"""Deterministic extraction of candidate party names from free text.

It favours recall over precision.  Its candidates go to the conflict check as a backstop, so a
name the triage step failed to list is still checked, and the rules baseline uses it as its
party list.
"""

from __future__ import annotations

import re

from intake_agent.conflicts import normalise_name

_WORD = "[A-Z][a-z]+(?:[-'\u2019][A-Z]?[a-z]+)*\\.?"
_INITIAL = r"[A-Z]\."
_RUN = re.compile(rf"(?:{_WORD}|{_INITIAL})(?:\s+(?:(?:{_WORD}|{_INITIAL}|&)|(?:and|of)(?=\s+[A-Z])))*")
_INITIAL_TOKEN = re.compile(_INITIAL)
_TITLED = re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.?\s+({_WORD}(?:\s+{_WORD})?)")

# Capitalised words that are not part of a party's name in these enquiries.
_STOPWORD_TEXT = """
i im i'm my me we our us you your he she they his her their it its the a an this that these those
hi hello dear regards kind thanks thank cheers sincerely yours best warm please
monday tuesday wednesday thursday friday saturday sunday today tomorrow yesterday
january february march april may june july august september october november december
on in at after before last next since because about for from to with if when and or but so then also
can could would will just not no yes ok there here what who how where why
melbourne victoria vic australia australian sydney geelong ballarat bendigo
court county supreme magistrates magistrate federal circuit family commission fair work tribunal vcat
police centrelink ato tac worksafe legal aid lawyers lawyer solicitor quollridge firm
street st road rd avenue ave lane drive crescent
mr mrs ms miss mx dr
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def _clean(token: str) -> str:
    return token.strip(".,;:!?'\u2019").casefold()


def candidate_names(text: str) -> list[str]:
    """Return possible person and organisation names, in order of appearance, without duplicates."""
    found: list[str] = []
    for match in _TITLED.finditer(text):
        found.append(match.group(1).rstrip("."))
    for match in _RUN.finditer(text):
        segment: list[str] = []
        for token in [*match.group(0).split(), ""]:
            if token and (_INITIAL_TOKEN.fullmatch(token) or _clean(token) not in _STOPWORDS):
                segment.append(token)
                continue
            while segment and segment[-1] in {"&", "and", "of"}:
                segment.pop()
            while segment and segment[0] in {"&", "and", "of"}:
                segment.pop(0)
            if len(segment) >= 2:
                found.append(" ".join(segment).rstrip("."))
            segment = []
    unique: list[str] = []
    seen: set[str] = set()
    for name in found:
        key = normalise_name(name)
        if key and key not in seen:
            seen.add(key)
            unique.append(name)
    return unique

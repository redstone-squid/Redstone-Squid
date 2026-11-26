"""Normalize and split searchable text the same way for documents and queries.

A term is a run of letters and digits after case folding and accent removal, so
"Glass-Door" yields `glass` and `door`, and 2x2 written with a multiplication sign
yields `2x2`. Symbols and emoji never become terms. Each token remembers where it
came from in the original text, which is what lets a match be highlighted without
searching the text again.
"""

import re
import unicodedata
from dataclasses import dataclass

_TERM = re.compile(r"[^\W_]+")


@dataclass(frozen=True, slots=True)
class Token:
    """A term and the half-open character range it spans in the original text."""

    term: str
    start: int
    end: int


def tokenize(value: str) -> tuple[Token, ...]:
    """Split text into normalized terms with their offsets in `value`."""
    normalized, offsets = _fold(value)
    return tuple(
        Token(match.group(), offsets[match.start()], offsets[match.end() - 1] + 1)
        for match in _TERM.finditer(normalized)
    )


def terms(value: str) -> tuple[str, ...]:
    """Return the normalized terms of `value` in order."""
    return tuple(token.term for token in tokenize(value))


def normalize_text(value: str) -> str:
    """Fold text for whole-value comparisons, such as `restriction:"No Entities"`."""
    return " ".join(terms(value))


def _fold(value: str) -> tuple[str, list[int]]:
    """Fold case, accents and the multiplication sign, mapping each output character to its source."""
    characters: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(value):
        for folded in unicodedata.normalize("NFKD", character.casefold().replace("\N{MULTIPLICATION SIGN}", "x")):
            if unicodedata.combining(folded):
                continue
            characters.append(folded)
            offsets.append(index)
    return "".join(characters), offsets

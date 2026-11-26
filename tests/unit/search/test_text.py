"""Tests for the tokenizer shared by indexing, querying and highlighting."""

from squid.core.search.domain.text import Token, normalize_text, terms, tokenize


def test_tokens_fold_case_accents_and_the_multiplication_sign() -> None:
    assert terms("Pístón 2\N{MULTIPLICATION SIGN}2 Glass-Door") == ("piston", "2x2", "glass", "door")


def test_symbols_and_emoji_never_become_terms() -> None:
    assert terms("🤖 No Observers ?x6 Door* · Locational") == ("no", "observers", "x6", "door", "locational")


def test_offsets_point_into_the_original_text() -> None:
    text = "🤖 Café Door"
    tokens = tokenize(text)

    assert tokens == (Token("cafe", 2, 6), Token("door", 7, 11))
    assert [text[token.start : token.end] for token in tokens] == ["Café", "Door"]


def test_whole_values_compare_after_normalization() -> None:
    assert normalize_text("  No   Entities ") == normalize_text("no entities") == "no entities"

"""Combined presentations retain source ranges and explicit loss declarations."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid_i18n import tree
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.semantics import check_alternative
from squid_ui.planning.text_presentations import ResolvedText, UnsupportedText, resolve_presentation_text
from squid_ui.presentations import alternative, alternatives, equivalent, losses, present_text, ref, shorten
from squid_ui.text import NEUTRAL, Localization


def test_combined_text_checks_each_shortening_separately() -> None:
    content = sl.stack(sl.paragraph("Six blocks wide", key="size"), sl.paragraph("Java", key="edition"))
    operation = shorten("size", max_chars=3)
    compact = present_text(operation, " / ", ref("edition"), key="summary")
    choice = alternative(compact, key="compact", fidelity=losses(operation))
    checked = check_alternative(alternatives(choice, content=content, key="build"), choice)
    result = resolve_presentation_text(checked, ref("summary"), localization=NEUTRAL)
    assert isinstance(result, ResolvedText)
    assert "".join(tree.plain(node) for node in result.nodes) == "Six / Java"
    assert result.fragments[0].source == ref("@0", "size")
    assert (result.fragments[0].source_start, result.fragments[0].source_end) == (0, 3)
    assert result.fragments[0].removed_chars == 12
    assert result.fragments[2].removed_chars == 0
    assert checked.asserted == frozenset()


def test_arbitrary_prose_cannot_masquerade_as_checked_shortening() -> None:
    content = sl.paragraph("A long description", key="description")
    operation = shorten("description", max_chars=6)
    choice = alternative(sl.paragraph("A long", key="description"), key="compact", fidelity=losses(operation))
    group = alternatives(choice, content=content, key="summary")
    with pytest.raises(LayoutInvariantError, match="source-referenced text fragments"):
        check_alternative(group, choice)
    asserted = replace(choice, fidelity=equivalent())
    assert check_alternative(group, asserted).asserted == frozenset({ref("description")})


def test_fragment_cannot_exceed_declared_loss() -> None:
    content = sl.paragraph("Description", key="description")
    operation = shorten("description", max_chars=8)
    choice = alternative(present_text(operation, key="text"), key="compact", fidelity=equivalent())
    group = alternatives(choice, content=content, key="summary")
    with pytest.raises(LayoutInvariantError, match="undeclared shortening"):
        check_alternative(group, choice)
    with pytest.raises(LayoutInvariantError, match="undeclared shortening"):
        check_alternative(group, replace(choice, fidelity=losses(shorten("description", max_chars=3))))


def test_shortening_uses_localized_text_and_preserves_styles() -> None:
    content = sl.paragraph(sl.strong(sl.tr(t"fragment-description = Original text")), key="description")
    operation = shorten("description", max_chars=4, keep="tail")
    choice = alternative(present_text(operation, key="text"), key="compact", fidelity=losses(operation))
    checked = check_alternative(alternatives(choice, content=content, key="summary"), choice)
    locale = Localization(locale="fr", resources=("fragment-description = Texte traduit",))
    result = resolve_presentation_text(checked, ref("text"), localization=locale)
    assert isinstance(result, ResolvedText)
    assert "".join(tree.plain(node) for node in result.nodes) == "duit"
    assert result.nodes[0].kind == "strong"
    assert result.fragments[0].source_length == len("Texte traduit")
    assert result.fragments[0].source_start == len("Texte traduit") - 4


def test_zero_length_shortening_has_an_empty_source_range() -> None:
    content = sl.paragraph("abc", key="description")
    operation = shorten("description", max_chars=0, keep="tail")
    choice = alternative(present_text(operation, key="text"), key="empty", fidelity=losses(operation))
    checked = check_alternative(alternatives(choice, content=content, key="summary"), choice)
    result = resolve_presentation_text(checked, ref("text"), localization=NEUTRAL)
    assert isinstance(result, ResolvedText)
    assert result.nodes == ()
    assert (result.fragments[0].source_start, result.fragments[0].source_end) == (3, 3)
    assert result.fragments[0].removed_chars == 3


def test_full_fragments_keep_links_and_shortening_reports_unsupported_behavior() -> None:
    content = sl.paragraph(sl.text_link("Documentation", "https://example.com"), key="docs")
    full = alternative(present_text(ref("docs"), key="text"), key="full", fidelity=equivalent())
    operation = shorten("docs", max_chars=3)
    compact = alternative(present_text(operation, key="text"), key="compact", fidelity=losses(operation))
    group = alternatives(full, compact, content=content, key="help")
    checked = check_alternative(group, full)
    result = resolve_presentation_text(checked, ref("text"), localization=NEUTRAL)
    assert isinstance(result, ResolvedText)
    assert result.nodes[0].kind == "link"
    assert dict(result.nodes[0].attrs)["href"] == "https://example.com"
    rejected = resolve_presentation_text(check_alternative(group, compact), ref("text"), localization=NEUTRAL)
    assert rejected == UnsupportedText(ref("docs"), "link")


def test_field_fragments_must_preserve_labels_as_well_as_values() -> None:
    content = sl.fields(sl.field("Width", "3 blocks", key="width"))
    value_only = alternative(present_text(ref("width", "value"), key="text"), key="bare", fidelity=equivalent())
    group = alternatives(value_only, content=content, key="build")
    with pytest.raises(LayoutInvariantError, match="omissions"):
        check_alternative(group, value_only)
    labeled = replace(value_only, node=present_text(ref("width", "label"), ": ", ref("width", "value"), key="text"))
    checked = check_alternative(group, labeled)
    result = resolve_presentation_text(checked, ref("text"), localization=NEUTRAL)
    assert isinstance(result, ResolvedText)
    assert "".join(tree.plain(node) for node in result.nodes) == "Width: 3 blocks"

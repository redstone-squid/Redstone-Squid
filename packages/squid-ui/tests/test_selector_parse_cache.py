"""Compiled selector reuse cannot confuse registries or retain types without a bound."""

import gc
import weakref
from collections.abc import Iterator
from typing import override

import pytest

import squid_ui as ui
from squid_ui import semantic as s
from squid_ui.selectors import SelectorSyntaxError, parse, query
from squid_ui.selectors.parser import _parse_cached


@pytest.fixture(autouse=True)
def empty_parser_cache() -> Iterator[None]:
    _parse_cached.cache_clear()
    yield
    _parse_cached.cache_clear()


def test_repeated_source_reuses_compilation_without_caching_matches() -> None:
    source = 'Paragraph[key="message"]'
    selector = parse(source)
    assert parse(source) is selector
    assert parse(source, types={}) is selector
    assert query(ui.paragraph("Ready", key="message"), selector).exists()
    assert not query(ui.paragraph("Ready", key="other"), selector).exists()


def test_registry_order_and_mapping_identity_do_not_affect_reuse() -> None:
    first = parse("Control", types={"Control": s.ActionControl, "Text": s.Paragraph})
    assert parse("Control", types={"Text": s.Paragraph, "Control": s.ActionControl}) is first


def test_registry_mutation_preserves_previously_compiled_type_meaning() -> None:
    registry: dict[str, type] = {"Entry": s.Paragraph}
    paragraph = parse("Entry", types=registry)
    registry["Entry"] = s.Heading
    heading = parse("Entry", types=registry)
    assert paragraph is not heading
    assert query(ui.paragraph("Ready"), paragraph).exists()
    assert not query(ui.paragraph("Ready"), heading).exists()
    assert query(ui.heading("Ready"), heading).exists()
    registry["Entry"] = s.Paragraph
    assert parse("Entry", types=registry) is paragraph


def test_type_identity_wins_over_metaclass_equality_and_hashing() -> None:
    class EqualTypes(type):
        @override
        def __eq__(self, other: object) -> bool:
            return isinstance(other, EqualTypes)

        @override
        def __hash__(self) -> int:
            raise TypeError("unhashable registered type")

    class First(metaclass=EqualTypes): ...

    class Second(metaclass=EqualTypes): ...

    assert First == Second
    first = parse("Entry", types={"Entry": First})
    second = parse("Entry", types={"Entry": Second})
    assert first is not second
    assert parse("Entry", types={"Entry": First}) is first


def test_failed_parse_keeps_source_diagnostics_and_does_not_poison_registry() -> None:
    for _ in range(2):
        with pytest.raises(SelectorSyntaxError, match=r"unknown type.*offset 0") as caught:
            parse("Entry")
        assert str(caught.value).endswith("Entry\n^")
    assert _parse_cached.cache_info().currsize == 0
    assert query(ui.paragraph("Ready"), parse("Entry", types={"Entry": s.Paragraph})).exists()
    with pytest.raises(SelectorSyntaxError, match="expected"):
        parse("Paragraph[")


def test_eviction_releases_registered_types() -> None:
    def register() -> weakref.ReferenceType[type]:
        class Temporary: ...

        parse("Temporary", types={"Temporary": Temporary})
        return weakref.ref(Temporary)

    reference = register()
    gc.collect()
    assert reference() is not None
    capacity = _parse_cached.cache_info().maxsize
    assert capacity is not None
    for index in range(capacity):
        parse(f'Paragraph[key="entry-{index}"]')
    gc.collect()
    assert _parse_cached.cache_info().currsize == capacity
    assert reference() is None

"""String selectors compile to the same scoped operations as typed selectors."""

import pytest

import squid_ui as ui
from squid_ui import semantic as s
from squid_ui.selectors import SelectorSyntaxError, parse, query, select, snapshot


async def pressed(event: ui.PressEvent) -> None: ...


def tree() -> object:
    return ui.stack(
        ui.section(
            ui.heading("Editor"),
            s.ActionControls(
                (
                    s.ActionControl("save", "Save", pressed),
                    s.ActionControl("cancel", "Cancel", pressed, available=False),
                ),
                key="toolbar",
            ),
        ),
        ui.paragraph("Ready"),
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('ActionControl[key="save"]', ["save"]),
        ("ActionControl[available=false]", ["cancel"]),
        ('ActionControl[label*="av"]', ["save"]),
        ('Section ActionControl:not([key="cancel"])', ["save"]),
        ("ActionControls > ActionControl", ["save", "cancel"]),
        ('ActionControl:is([key="cancel"], [key="save"])', ["save", "cancel"]),
        ('ActionControl[key="cancel"], ActionControl[key="save"]', ["save", "cancel"]),
        ("ActionControl, ActionControl", ["save", "cancel"]),
        ('ActionControls:has(> ActionControl[key="save"])', ["toolbar"]),
        ("ActionControls:has(:scope > ActionControl)", ["toolbar"]),
        ('Section [key="save"]', ["save"]),
        ("Section :is(ActionControl)", ["save", "cancel"]),
        ("ActionControl[guard=null]", ["save", "cancel"]),
        ("ActionControl[available]", ["save", "cancel"]),
    ],
)
def test_css_subset(source: str, expected: list[str]) -> None:
    found = query(snapshot(tree()), parse(source)).all()
    assert [match.key for match in found] == expected


def test_scoped_has_cannot_match_an_outer_ancestor() -> None:
    assert not query(snapshot(tree()), parse("Section:has(Stack ActionControl)")).exists()
    assert query(snapshot(tree()), parse('Paragraph[text="Ready"]')).count() == 1
    assert query(snapshot(tree()), parse("Heading[level=2]")).count() == 1
    assert query(snapshot(tree()), parse("Heading[level=2e0]")).count() == 1
    assert query(snapshot(tree()), parse("Thing", types={"Thing": s.ActionControl})).count() == 2


def test_parser_and_builder_have_identical_results() -> None:
    captured = snapshot(tree())
    typed = select(s.Section).descendants(select(s.ActionControl).key("save"))
    assert query(captured, typed).all() == query(captured, parse('Section ActionControl[key="save"]')).all()


@pytest.mark.parametrize(
    "source",
    [
        "",
        "Unknown",
        "ActionControl + ActionControl",
        "ActionControl ~ ActionControl",
        "#save",
        ".button",
        "ActionControl[",
        "ActionControl[key=save]",
        'ActionControl[key="save]',
        "ActionControl,",
        "ActionControl >",
        "ActionControl:visible",
        "ActionControl:has()",
        "ActionControl)",
        'ActionControl[key*="save"]',
        "ActionControl[label=2]",
        'ActionControl[available^="t"]',
        "__import__(os)",
        'ActionControl[key="\\q"]',
        "ActionControl[key=null]",
    ],
)
def test_invalid_syntax_has_source_position(source: str) -> None:
    with pytest.raises(SelectorSyntaxError, match="offset") as error:
        parse(source)
    assert "^" in str(error.value)


def test_nested_pseudo_classes_and_relative_has_lists() -> None:
    captured = snapshot(tree())
    result = query(captured, parse('ActionControls:not(:has([key="missing"])):has(> Heading, > ActionControl)'))
    assert result.one().key == "toolbar"
    with pytest.raises(SelectorSyntaxError, match="nesting"):
        parse(":is(" * 65 + "*" + ")" * 65)


def test_presence_uses_query_metadata_and_own_text() -> None:
    captured = snapshot(tree())
    assert query(captured, parse("[qualified_key]")).count() == 3
    assert query(captured, parse("[text]")).count() == 2
    assert query(captured, parse("Heading[level=2e0]")).count() == 1


def test_query_accepts_strings_and_preserves_scope() -> None:
    captured = snapshot(tree())
    section = query(captured, "Section").one()
    source = 'ActionControl[key="save"]'
    assert query(captured, source, within=section).all() == query(captured, parse(source), within=section).all()
    assert query(captured, ":scope", within=section, include_scope=True).one() == section
    assert not query(captured, "Stack ActionControl", within=section).exists()
    assert query(captured, "Control", types={"Control": s.ActionControl}).count() == 2
    with pytest.raises(SelectorSyntaxError, match="unknown type"):
        query(captured, "Control")
    with pytest.raises(SelectorSyntaxError, match="offset"):
        query(captured, "ActionControl[")

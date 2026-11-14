"""Factories and directly constructed models render deferred templates consistently."""

import pytest

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid_i18n import Localization, Text, resolve_text, source_text, tr
from squid_ui.forms import ChoiceOption, FormSpec, FormText, TextField
from squid_ui.planning import PlanCache, plan
from squid_ui.primitives import Text as PrimitiveText
from squid_ui_discord import DISCORD_V2_DPY27
from squid_ui_discord.form_planning import FieldPlan, StaticTextPlan, plan_form


def test_semantic_and_discord_factories_normalize_text() -> None:
    name = "*Ann*"
    template = t"Hello {name}"
    values = (
        sl.paragraph(template).content,
        sd.v2.text(template).content,
        sd.v2.heading(template).content,
        sd.v2.footer(template).content,
        sd.v2.code(template).content,
        sd.v2.lines(template).lines[0],
        sd.v2.option(template, "ann", description=template).description,
        sd.v2.link_button(template, "https://example.org").label,
        sd.v2.thumbnail("https://example.org/image.png", description=template).description,
        sd.classic.content(template).content,
        sd.classic.card_field(template, template).name,
        sd.classic.card_footer(template).text,
    )
    for value in values:
        assert isinstance(value, Text)
        assert source_text(value) == "Hello *Ann*"


def test_bare_template_children_are_one_text_node() -> None:
    name = "Ann"
    panel = sd.v2.panel(t"Hello {name}")
    section = sd.v2.section(t"Hello {name}", accessory=sd.v2.thumbnail("https://example.org/image.png"))
    for child in (panel.children[0], section.texts[0]):
        assert isinstance(child, PrimitiveText)
        assert isinstance(child.content, Text)
        assert source_text(child.content) == "Hello Ann"


def test_forms_and_widgets_render_template_inputs_in_the_requested_locale() -> None:
    label = tr("Hello", key="hello")
    template = t"Field: {label}"
    field = TextField(template, key="name", placeholder=template, description=template)
    spec = FormSpec(template, (FormText(template), field))
    option = ChoiceOption("name", template, "name", description=template)
    entry = sp.MenuEntry(template, template)
    menu = sp.Menu(template, (entry,))
    french = Localization("fr", ("hello = Bonjour",))
    for value in (spec.title, field.label, field.placeholder, field.description, option.label, entry.label, menu.title):
        assert value is not None
        assert resolve_text(value, french).plain() == "Field: Bonjour"
    rendered = plan_form(spec, localization=french)
    assert rendered.title == "Field: Bonjour"
    static, control = rendered.items
    assert isinstance(static, StaticTextPlan)
    assert static.content == "Field: Bonjour"
    assert isinstance(control, FieldPlan)
    assert control.label == "Field: Bonjour"
    assert control.description == "Field: Bonjour"
    assert field.default is None


@pytest.mark.parametrize("direct", [False, True])
def test_template_normalization_preserves_cache_identity_and_locale(*, direct: bool) -> None:
    def document(name: str) -> sl.semantic.Paragraph:
        greeting = tr("Hello", key="hello")
        template = t"{greeting}, {name}"
        return sl.semantic.Paragraph(template) if direct else sl.paragraph(template)

    cache = PlanCache()
    french = Localization("fr", ("hello = Bonjour",))
    first = plan(document("Ann"), target=DISCORD_V2_DPY27, localization=french, cache=cache)
    same = plan(document("Ann"), target=DISCORD_V2_DPY27, localization=french, cache=cache)
    changed = plan(document("Bob"), target=DISCORD_V2_DPY27, localization=french, cache=cache)
    fresh = plan(document("Bob"), target=DISCORD_V2_DPY27, localization=french)
    assert same.metrics.cache_hit
    assert same.scene == first.scene
    assert changed.scene == fresh.scene
    assert changed.scene != first.scene


def test_existing_text_and_absent_labels_are_preserved() -> None:
    label = tr("Hello", key="hello")
    assert sd.v2.text(label).content is label
    assert sl.paragraph(label).content is label
    assert sd.v2.link_button(None, "https://example.org").label is None
    assert TextField(key="name").label is None

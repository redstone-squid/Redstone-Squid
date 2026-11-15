"""Chrome wording belongs to the host, with catalog-independent English defaults."""

from squid_ui.chrome import Chrome, localize_chrome
from squid_ui.content.text import NEUTRAL, Localization, resolve_text, tr
from squid_ui.interactions.forms import FormSpec, IntField, TextField
from squid_ui.planning.pagination.navigation import page_jump_form


def test_default_chrome_does_not_consume_framework_message_ids() -> None:
    localization = Localization(
        "fr",
        ("ui-chrome-next = Suivant\nui-chrome-page-footer = Page française",),
    )
    chrome = localize_chrome(Chrome(), localization)
    assert chrome.next == "Next"
    assert resolve_text(chrome.page_footer(2, 3), localization).plain() == "Page 2 of 3"
    assert resolve_text(chrome.try_again_in(0.5), localization).plain() == "Try again in 1 second."
    assert resolve_text(chrome.try_again_in(1.5), localization).plain() == "Try again in 2 seconds."


def test_host_can_supply_plain_or_deferred_localized_wording() -> None:
    localization = Localization("fr", ("app-next = Suivant",))
    chrome = localize_chrome(
        Chrome(
            next=tr(t"app-next = Next"),
            previous="Précédent",
            page_footer=lambda page, pages: f"Page {page} sur {pages}",
        ),
        localization,
    )
    assert chrome.next == "Suivant"
    assert chrome.previous == "Précédent"
    assert resolve_text(chrome.page_footer(2, 3), localization).plain() == "Page 2 sur 3"


async def test_form_wording_can_be_overridden_per_field_or_submission() -> None:
    field = TextField(key="name", label="Name", minimum=3, chrome=Chrome(form_required="Name is missing"))
    form = FormSpec("Profile", (field,))
    missing = await form.evaluate({})
    assert resolve_text(missing.errors[0].message, NEUTRAL).plain() == "Name is missing"

    host = Chrome(form_text_minimum=lambda minimum: f"Use {minimum} letters")
    short = await form.evaluate({"name": "Al"}, chrome=host)
    assert resolve_text(short.errors[0].message, NEUTRAL).plain() == "Use 3 letters"
    assert field.chrome.form_required == "Name is missing"


def test_page_jump_uses_host_wording_and_validation() -> None:
    chrome = Chrome(
        jump_to_page="Go to page", page_jump_number="Destination", page_jump_range=lambda pages: f"1-{pages}"
    )
    form = page_jump_form(0, 10, chrome=chrome)
    assert form.title == "Go to page"
    field = form.items[0]
    assert isinstance(field, IntField)
    assert field.label == "Destination"
    assert field.description == "1-10"
    assert field.chrome is chrome

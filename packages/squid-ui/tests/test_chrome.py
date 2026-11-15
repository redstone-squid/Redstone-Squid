"""Chrome wording belongs to the host, with catalog-independent English defaults."""

from squid_ui.chrome import Chrome, localize_chrome
from squid_ui.content.text import Localization, resolve_text, tr


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

"""Every public framework name appears in its package's reference page.

The reference pages under docs/reference/ are hand-curated so readers meet the important
names first, which means nothing regenerates them when ``__all__`` grows. This scan turns
that curation into a checked contract: a new public name must be placed in its page (a
``:::`` directive, or a table row for submodules) before it ships.
"""

import re
from pathlib import Path

import pytest

PAGES = {
    "squid_reactivity": Path("docs/reference/squid-reactivity.md"),
    "squid_ui": Path("docs/reference/squid-ui.md"),
    "squid_ui_widgets": Path("docs/reference/squid-ui-widgets.md"),
    "squid_ui_discord": Path("docs/reference/squid-ui-discord.md"),
    "squid_storage": Path("docs/reference/squid-storage.md"),
    "squid_replication": Path("docs/reference/squid-replication.md"),
}


@pytest.mark.parametrize("package_name", sorted(PAGES))
def test_reference_page_covers_the_public_surface(package_name: str) -> None:
    module = __import__(package_name)
    page = PAGES[package_name].read_text(encoding="utf-8")
    # A name counts as covered when it appears at the end of a dotted path: its own
    # directive, a table row, or a canonical-home directive for a re-export.
    missing = [name for name in module.__all__ if not re.search(rf"[\w.]+\.{re.escape(name)}\b", page)]
    assert missing == [], f"public names absent from {PAGES[package_name]}: {missing}"

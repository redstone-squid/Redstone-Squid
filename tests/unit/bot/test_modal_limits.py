"""Regression test: modals clamp to Discord limits at serialization time.

A build with many stored URLs produced a `SubmissionDetailsModal` whose TextInput `default`
exceeded Discord's 4000-char cap, so `send_modal` failed with HTTP 50035 (Invalid Form Body).
`ErrorHandledModal.to_dict` now conforms every modal on its way out.
"""

import discord

from squid.bot.errors import ErrorHandledModal
from squid_layouts import assert_within_limits


def test_oversized_defaults_and_title_are_clamped_at_serialization():
    modal = ErrorHandledModal(title="Links and optional details, plus " + "x" * 60, timeout=None)
    urls = discord.ui.TextInput(
        label="images",
        default=", ".join(f"https://example.invalid/image-{index}.png" for index in range(300)),
        required=False,
    )
    modal.add_item(discord.ui.Label(text="Images", component=urls))

    payload = modal.to_dict()

    assert len(payload["title"]) <= 45
    assert len(payload["components"][0]["component"]["value"]) <= 4000
    assert_within_limits(modal)


def test_fitting_modal_is_untouched():
    modal = ErrorHandledModal(title="Edit", timeout=None)
    field = discord.ui.TextInput(label="name", default="steve")
    modal.add_item(discord.ui.Label(text="Name", component=field))

    payload = modal.to_dict()

    assert payload["title"] == "Edit"
    assert field.default == "steve"

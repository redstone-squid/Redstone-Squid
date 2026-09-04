"""Public-safe and deferred-localization contracts for media errors."""

from uuid import UUID

import pytest

from squid.core.i18n import tr
from squid.media.errors import (
    DraftMediaConflictError,
    DraftMediaNotFoundError,
    DraftMediaRequestError,
    DraftMediaUnavailableError,
)
from squid_ui.text import Localization, localization_scope

UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_public_attachment_errors_localize_title_detail_and_action_at_render_time(locale: str) -> None:
    errors = (
        DraftMediaRequestError("content_length_invalid"),
        DraftMediaNotFoundError(UPLOAD_ID),
        DraftMediaConflictError(UPLOAD_ID),
        DraftMediaUnavailableError(),
    )
    localization = Localization(
        locale=locale,
        gettext=lambda message: f"{locale}:{message}",
        ngettext=lambda singular, plural, count: singular if count == 1 else plural,
    )

    with localization_scope(localization):
        for error in errors:
            assert tr(error.title).startswith(f"{locale}:")
            assert tr(error.message).startswith(f"{locale}:")
            assert error.end_user_action is not None
            assert tr(error.end_user_action).startswith(f"{locale}:")

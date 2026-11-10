"""Public-safe and deferred-localization contracts for media errors."""

import inspect
from uuid import UUID

import pytest
from fluent.syntax import ast as fluent_ast

import squid.media.errors as media_errors
from squid.core.errors import SquidError
from squid.core.i18n import locales_dir, localization_for
from squid.media.domain import MediaLimitMeasure, MediaViolation
from squid.media.errors import (
    DraftMediaConflictError,
    DraftMediaNotFoundError,
    DraftMediaRequestError,
    DraftMediaUnavailableError,
    MediaDraftNotFoundError,
    MediaDraftRevisionConflictError,
    MediaDraftStateConflictError,
    MediaLimitExceededError,
)
from squid_i18n import Markup, source_text
from squid_i18n.compiler import parse_resource
from squid_ui.text import Localization, Message, localization_scope

UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")
PUBLIC_ATTACHMENT_ERROR_COPY: dict[type[SquidError], dict[str, str]] = {
    MediaLimitExceededError: {
        "title": "Invalid value",
        "message": "The attachment exceeds one or more processing limits.",
        "end_user_action": "Choose fewer, smaller, or less resource-intensive attachments and try again.",
    },
    MediaDraftStateConflictError: {
        "title": "Draft media locked",
        "message": "Media cannot be changed while this submission draft is locked.",
        "end_user_action": "Reload the draft before trying again.",
    },
    MediaDraftRevisionConflictError: {
        "title": "Draft changed",
        "message": "The submission draft changed while its attachment was uploading.",
        "end_user_action": "Reload the draft and upload the attachment again.",
    },
    MediaDraftNotFoundError: {
        "title": "Draft not found",
        "message": "Submission draft not found.",
        "end_user_action": "Reload your drafts before uploading the attachment again.",
    },
    DraftMediaRequestError: {
        "title": "Invalid attachment upload",
        "message": "The draft attachment upload request is invalid.",
        "end_user_action": "Check the attachment type and upload it again.",
    },
    DraftMediaNotFoundError: {
        "title": "Attachment not found",
        "message": "Draft attachment not found.",
        "end_user_action": "Reload the draft and choose one of its current attachments.",
    },
    DraftMediaConflictError: {
        "title": "Attachment upload conflict",
        "message": "The attachment upload identifier is already in use.",
        "end_user_action": "Retry the upload with a new attachment identifier.",
    },
    DraftMediaUnavailableError: {
        "title": "Attachment processing unavailable",
        "message": "Draft attachment processing is temporarily unavailable.",
        "end_user_action": "Keep the attachment locally and try uploading it again later.",
    },
}


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_public_attachment_errors_localize_title_detail_and_action_at_render_time(locale: str) -> None:
    errors = (
        DraftMediaRequestError("content_length_invalid"),
        DraftMediaNotFoundError(UPLOAD_ID),
        DraftMediaConflictError(UPLOAD_ID),
        DraftMediaUnavailableError(),
    )
    resources = tuple(
        message.declaration.source.replace(" = ", f" = {locale}:", 1)
        for error in errors
        for message in (error.title, error.message, error.end_user_action)
        if isinstance(message, Message) and message.declaration is not None
    )
    localization = Localization(locale=locale, resources=resources)

    with localization_scope(localization):
        for error in errors:
            assert isinstance(error.title, Message)
            assert isinstance(error.message, Message)
            assert isinstance(error.end_user_action, Message)
            assert error.title.resolve().startswith(f"{locale}:")
            assert error.message.resolve().startswith(f"{locale}:")
            assert error.end_user_action.resolve().startswith(f"{locale}:")


def _public_attachment_errors() -> dict[type[SquidError], SquidError]:
    errors: tuple[SquidError, ...] = (
        MediaLimitExceededError(MediaViolation(MediaLimitMeasure.SOURCE_BYTES, 9, 8)),
        MediaDraftStateConflictError("locked"),
        MediaDraftRevisionConflictError(expected=1, actual=2),
        MediaDraftNotFoundError(UPLOAD_ID),
        DraftMediaRequestError("content_length_invalid"),
        DraftMediaNotFoundError(UPLOAD_ID),
        DraftMediaConflictError(UPLOAD_ID),
        DraftMediaUnavailableError(),
    )
    return {type(error): error for error in errors}


def _catalog_keys(locale: str) -> set[str]:
    return {
        entry.id.name
        for entry in parse_resource((locales_dir() / locale / "messages.ftl").read_text()).body
        if isinstance(entry, fluent_ast.Message)
    }


def test_public_attachment_copy_inventory_covers_the_error_namespace() -> None:
    namespaced = {
        error_type
        for name, error_type in inspect.getmembers(media_errors, inspect.isclass)
        if error_type.__module__ == media_errors.__name__
        and (name.startswith(("DraftMedia", "MediaDraft")) or name == "MediaLimitExceededError")
    }

    assert namespaced == PUBLIC_ATTACHMENT_ERROR_COPY.keys()
    assert _public_attachment_errors().keys() == PUBLIC_ATTACHMENT_ERROR_COPY.keys()


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_public_attachment_error_copy_is_complete_in_real_catalogs(locale: str) -> None:
    errors = _public_attachment_errors()
    localization = localization_for(locale)
    catalog_keys = _catalog_keys(locale)

    with localization_scope(localization):
        for error_type, expected_copy in PUBLIC_ATTACHMENT_ERROR_COPY.items():
            error = errors[error_type]
            for field, template in expected_copy.items():
                message = getattr(error, field)
                assert isinstance(message, Message)
                assert source_text(message, markup=Markup.PLAIN) == template
                assert message.declaration is not None
                assert message.declaration.identifier in catalog_keys
                rendered = message.resolve()
                if locale == "zh-CN":
                    assert rendered != template

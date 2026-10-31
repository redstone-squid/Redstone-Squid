"""Shared and context error tests."""

from uuid import UUID

from squid.accounts.errors import AccountAlreadyLinkedError
from squid.builds.errors import BuildNotFoundError, InvalidBuildError
from squid.core.errors import ErrorCode, InvalidStateError
from squid.core.i18n import localization_for, tr
from squid_ui.text import localization_scope

MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")


def test_error_separates_backend_and_public_details() -> None:
    error = InvalidBuildError(
        "Build dimensions are invalid.",
        developer_action="Inspect the parsed dimensions.",
        end_user_action="Enter width, height, and depth.",
    )

    assert str(error) == "Build dimensions are invalid. Inspect the parsed dimensions."
    assert error.public_detail() == "Build dimensions are invalid. Enter width, height, and depth."


def test_a_listed_message_keeps_the_developer_action_on_its_own_line() -> None:
    """Configuration failures render one finding per line; the action is not a list item."""
    error = InvalidBuildError(
        "Two dimensions are invalid:\n  - width\n  - depth",
        developer_action="Inspect the parsed dimensions.",
    )

    assert str(error).splitlines()[-1] == "Inspect the parsed dimensions."


def test_with_context_mutates_exception_without_changing_type() -> None:
    error = BuildNotFoundError(42)

    updated = error.with_context(context={"operation": "confirm"}, public_context={"source": "command"})

    assert updated is error
    assert updated.context == {"build_id": 42, "operation": "confirm"}
    assert updated.public_context == {"build_id": 42, "source": "command"}
    assert updated.code is ErrorCode.BUILD_NOT_FOUND


def test_with_context_can_restate_the_message() -> None:
    """Enrichment that cannot restate the message is only half a helper.

    A layer that resolves *what* a conflict was usually wants to say so, and the alternative was
    assigning `message` by hand, which skips the `args` refresh and leaves `str(error)` stale.
    """
    error = BuildNotFoundError(42)
    build_id = 42

    updated = error.with_context(message=tr(t"Build {build_id} was deleted while you were editing it."))

    assert "Build 42 was deleted" in updated.public_detail()
    assert "Build 42 was deleted" in str(updated)
    assert updated.args == (updated.backend_detail(),)


def test_with_context_replaces_the_complete_deferred_message() -> None:
    build_id = 42
    holder = "someone else"
    error = BuildNotFoundError(build_id)

    updated = error.with_context(message=tr(t"Build {build_id} is held by {holder}."))

    assert "Build 42 is held by someone else." in updated.public_detail()


def test_context_and_public_context_are_separate() -> None:
    error = AccountAlreadyLinkedError(account_id=123, minecraft_uuid=MINECRAFT_UUID)

    assert error.context == {"account_id": 123, "minecraft_uuid": str(MINECRAFT_UUID)}
    assert error.public_context == {}
    assert str(MINECRAFT_UUID) not in error.public_detail()


def test_semantic_errors_retain_builtin_compatibility() -> None:
    assert isinstance(InvalidBuildError(), ValueError)
    assert isinstance(InvalidStateError(), RuntimeError)
    assert isinstance(BuildNotFoundError(1), LookupError)


def test_deferred_message_params_render_in_backend_and_public_detail() -> None:
    from squid.builds.errors import RestrictionNotFoundError

    error = RestrictionNotFoundError("no-pistons")

    assert error.backend_detail() == "Restriction 'no-pistons' does not exist."
    assert error.public_detail() == "Restriction 'no\\-pistons' does not exist."


def test_ambient_title_and_public_detail_fall_back_to_english() -> None:
    from squid.builds.errors import BuildNotFoundError as _BuildNotFoundError

    error = _BuildNotFoundError(42)

    with localization_scope(localization_for("en")):
        assert tr(error.title) == "Resource not found"
        assert error.public_detail() == "Build not found. Check the build ID and try again."
    # Unsupported locales bind the English catalog rather than erroring.
    with localization_scope(localization_for("fr")):
        assert error.public_detail() == "Build not found. Check the build ID and try again."

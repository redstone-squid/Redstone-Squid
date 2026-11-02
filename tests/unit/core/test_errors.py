"""Shared and context error tests."""

from uuid import UUID

from squid.accounts.domain import IdentityProvider
from squid.accounts.errors import AccountAlreadyLinkedError
from squid.builds.errors import BuildNotFoundError, InvalidBuildError
from squid.core.errors import ErrorCode, InvalidStateError

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


def test_context_and_public_context_are_separate() -> None:
    error = AccountAlreadyLinkedError(discord_id=123, minecraft_uuid=MINECRAFT_UUID)

    assert error.context == {
        "provider": IdentityProvider.DISCORD,
        "subject": "123",
        "minecraft_uuid": str(MINECRAFT_UUID),
    }
    assert error.public_context == {}
    assert str(MINECRAFT_UUID) not in error.public_detail()


def test_semantic_errors_retain_builtin_compatibility() -> None:
    assert isinstance(InvalidBuildError(), ValueError)
    assert isinstance(InvalidStateError(), RuntimeError)
    assert isinstance(BuildNotFoundError(1), LookupError)


def test_message_params_render_in_backend_and_public_detail() -> None:
    from squid.builds.errors import RestrictionNotFoundError

    error = RestrictionNotFoundError("no-pistons")

    assert error.backend_detail() == "Restriction 'no-pistons' does not exist."
    assert error.public_detail() == "Restriction 'no-pistons' does not exist."


def test_localized_title_and_public_detail_fall_back_to_english() -> None:
    from squid.builds.errors import BuildNotFoundError as _BuildNotFoundError

    error = _BuildNotFoundError(42)

    assert error.localized_title("en") == "Resource not found"
    assert error.localized_public_detail("en") == "Build not found. Check the build ID and try again."
    # Unsupported locale falls back to the English source text rather than erroring.
    assert error.localized_public_detail("fr") == "Build not found. Check the build ID and try again."

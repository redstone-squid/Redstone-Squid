"""Shared and context error tests."""

from uuid import UUID

from squid.builds.errors import BuildNotFoundError, InvalidBuildError
from squid.core.errors import ErrorCode, InvalidStateError
from squid.users.errors import AccountAlreadyLinkedError

MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")


def test_error_separates_backend_and_public_details() -> None:
    error = InvalidBuildError(
        "Build dimensions are invalid.",
        developer_action="Inspect the parsed dimensions.",
        end_user_action="Enter width, height, and depth.",
    )

    assert str(error) == "Build dimensions are invalid. Inspect the parsed dimensions."
    assert error.public_detail() == "Build dimensions are invalid. Enter width, height, and depth."


def test_with_context_mutates_exception_without_changing_type() -> None:
    error = BuildNotFoundError(42)

    updated = error.with_context(context={"operation": "confirm"}, public_context={"source": "command"})

    assert updated is error
    assert updated.context == {"build_id": 42, "operation": "confirm"}
    assert updated.public_context == {"build_id": 42, "source": "command"}
    assert updated.code is ErrorCode.BUILD_NOT_FOUND


def test_context_and_public_context_are_separate() -> None:
    error = AccountAlreadyLinkedError(123, MINECRAFT_UUID)

    assert error.context == {"discord_id": 123, "minecraft_uuid": str(MINECRAFT_UUID)}
    assert error.public_context == {}
    assert str(MINECRAFT_UUID) not in error.public_detail()


def test_semantic_errors_retain_builtin_compatibility() -> None:
    assert isinstance(InvalidBuildError(), ValueError)
    assert isinstance(InvalidStateError(), RuntimeError)
    assert isinstance(BuildNotFoundError(1), LookupError)

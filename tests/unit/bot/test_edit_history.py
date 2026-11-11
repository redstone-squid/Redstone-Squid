"""Shared saved-edit behavior, independent of the screen presenting it."""

import pytest

from squid.bot.edit_history import save_edit
from squid.core.errors import AuthorizationError, InvalidStateError
from squid_ui.runtime import History, HistoryResultStatus, transaction


async def test_saved_edits_share_noop_conflict_authorization_and_redo_rules() -> None:
    stored = {"value": 1, "unrelated": 9}
    allowed = True
    refreshed = []
    stack = History()

    async def authorize() -> bool:
        return allowed

    async def replace(expected: int, replacement: int) -> bool:
        if stored["value"] != expected:
            return False
        stored["value"] = replacement
        return True

    async def refresh() -> None:
        refreshed.append(stored["value"])

    async def save(before: int, after: int) -> bool:
        with transaction():
            return await save_edit(
                stack, "Edit", before, after, target="value", authorize=authorize, replace=replace, refresh=refresh
            )

    assert not await save(1, 1)
    assert not stack.entries
    assert await save(1, 2)
    assert refreshed == [2]
    allowed = False
    assert (await stack.undo()).status is HistoryResultStatus.CONFLICT
    assert stored == {"value": 2, "unrelated": 9}
    with pytest.raises(AuthorizationError):
        await save(2, 3)
    allowed = True
    assert (await stack.undo()).applied
    assert stored["value"] == 1
    assert (await stack.redo()).applied
    assert stored["value"] == 2
    stored["value"] = 4
    with pytest.raises(InvalidStateError):
        await save(2, 3)
    assert len(stack.entries) == 1
    assert stored == {"value": 4, "unrelated": 9}

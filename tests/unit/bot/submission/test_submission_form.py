"""Submit-button behaviour of the guided `/build submit` workspace."""

import asyncio
from typing import Any, cast

import discord
import pytest

from squid.bot.submission.ui.views import BuildSubmissionForm
from squid.builds.application import BuildService
from squid.builds.domain import BuildDraft


class _FakeResponse:
    """The one-shot interaction response slot, tracking how it was spent."""

    def __init__(self) -> None:
        self.deferred = False
        self.messages: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return self.deferred or bool(self.messages)

    async def defer(self, **kwargs: Any) -> None:
        self.deferred = True

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


class _FakeInteraction:
    def __init__(self) -> None:
        self.response = _FakeResponse()
        self.edits: list[dict[str, Any]] = []

    async def edit_original_response(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)


def _ready_form(**kwargs: Any) -> BuildSubmissionForm:
    draft = BuildDraft(door_orientation="Door", door_width=2, door_height=2)
    return BuildSubmissionForm(draft, cast(BuildService, object()), **kwargs)


async def _press_submit(form: BuildSubmissionForm, interaction: _FakeInteraction) -> None:
    await form.submit.callback(cast(discord.Interaction, interaction))


async def test_form_stays_open_after_a_failed_submission() -> None:
    """The filled-in draft only lives in this message, so a failure must leave it usable."""
    attempts = 0

    async def on_submit() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            msg = "vote channel exploded"
            raise RuntimeError(msg)

    form = _ready_form(on_submit=on_submit)
    interaction = _FakeInteraction()

    with pytest.raises(RuntimeError):
        await _press_submit(form, interaction)

    assert form.is_finished() is False
    assert form.value is None
    assert "try again" in str(form.to_components())
    assert interaction.edits[-1]["view"] is form

    await _press_submit(form, _FakeInteraction())

    assert attempts == 2
    assert form.value is True
    assert form.is_finished() is True


async def test_form_stops_only_once_the_submission_succeeds() -> None:
    submitted = False

    async def on_submit() -> None:
        nonlocal submitted
        submitted = True

    form = _ready_form(on_submit=on_submit)

    await _press_submit(form, _FakeInteraction())

    assert submitted is True
    assert form.value is True
    assert form.is_finished() is True


async def test_a_second_click_does_not_submit_twice() -> None:
    """An impatient double-click would otherwise persist the same build twice."""
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def on_submit() -> None:
        nonlocal attempts
        attempts += 1
        started.set()
        await release.wait()

    form = _ready_form(on_submit=on_submit)
    first = asyncio.create_task(_press_submit(form, _FakeInteraction()))
    await started.wait()

    second = _FakeInteraction()
    await _press_submit(form, second)

    assert attempts == 1
    assert second.response.deferred is False
    assert len(second.response.messages) == 1

    release.set()
    await first

    assert form.value is True


async def test_form_without_a_callback_defers_to_its_caller() -> None:
    form = _ready_form()

    await _press_submit(form, _FakeInteraction())

    assert form.value is True
    assert form.is_finished() is True

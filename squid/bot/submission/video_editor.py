"""Prepare video settings and activation rows, including detected input positions."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import cast, override

from pydantic import ValidationError

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.schematics.application.video import VideoInteraction, VideoPosition, VideoRenderRequest

type VideoSubmit = Callable[[sd.ActionEvent, VideoRenderRequest], Awaitable[None]]


class VideoEditor(sd.Screen[VideoRenderRequest]):
    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("video-recipe"))
    seconds: int = sl.state(6)
    orbit: float = sl.state(180.0)
    simulate: bool = sl.state(True)
    interactions: tuple[VideoInteraction, ...] = sl.state((VideoInteraction(tick=20),), persist=False)

    def __init__(
        self,
        recipe: VideoRenderRequest,
        *,
        candidates: tuple[VideoPosition, ...] = (),
        submit: VideoSubmit | None = None,
    ) -> None:
        self.recipe = recipe
        self.seconds = max(1, recipe.duration_ticks // 20)
        self.orbit = recipe.orbit_degrees
        self.simulate = recipe.simulation_enabled
        self.interactions = recipe.interactions
        self.submit = submit
        self.candidates = (
            sp.Browser(
                sl.sources.list_source(candidates),
                key="inputs",
                identity=lambda item: str(item),
                label=lambda item: " ".join(map(str, item)),
                detail=self._input_detail,
                page_size=10,
                title=tr(t"video-detected-inputs = Detected input positions"),
                empty=tr(t"video-no-candidates = No detected input positions are available."),
            )
            if candidates
            else None
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.heading(tr(t"video-editor-title = Video recipe")),
            sl.note(f"{self.seconds}s · {self.orbit:g}°"),
            sl.form(
                tr(t"video-settings = Clip settings"),
                sl.forms.FormSpec[sd.Request](
                    tr(t"video-settings = Clip settings"),
                    (
                        sl.forms.BoolField(
                            key="simulate", label=tr(t"video-simulate = Simulate the circuit"), default=self.simulate
                        ),
                        sl.forms.IntField(
                            key="seconds",
                            label=tr(t"video-seconds = Duration in seconds"),
                            minimum=1,
                            maximum=10,
                            default=self.seconds,
                        ),
                        sl.forms.FloatField(
                            key="orbit",
                            label=tr(t"video-orbit = Camera orbit in degrees"),
                            minimum=-360,
                            maximum=360,
                            default=self.orbit,
                        ),
                    ),
                ),
                key="settings",
                on_submit=self._settings,
            ),
        ]
        for index, item in enumerate(self.interactions):
            position = "Automatic input" if item.position is None else " ".join(map(str, item.position))
            nodes.append(
                sl.stack(
                    sl.paragraph(f"Tick {item.tick}: {position}"),
                    sl.action_controls(
                        sl.action_control(
                            tr(t"video-remove-activation = Remove activation"),
                            partial(self._remove, index),
                            key=f"remove-{index}",
                        ),
                        key=f"activation-{index}",
                    ),
                )
            )
        nodes.append(
            sl.form(
                tr(t"video-add-activation = Add activation"),
                sl.forms.FormSpec[sd.Request](
                    tr(t"video-add-activation = Add activation"),
                    (
                        sl.forms.IntField(
                            key="tick",
                            label=tr(t"video-tick = Tick (20 ticks per second)"),
                            minimum=0,
                            maximum=199,
                            default=20,
                        ),
                        *(
                            sl.forms.IntField(
                                key=axis,
                                label=axis.upper(),
                                required=False,
                                description=tr(
                                    t"video-coordinate-help = Leave all coordinates blank for automatic input detection."
                                ),
                            )
                            for axis in ("x", "y", "z")
                        ),
                    ),
                ),
                key="activation",
                on_submit=self._add,
            )
        )
        if self.candidates is not None:
            nodes.append(
                sl.note(tr(t"video-select-detected = Choose a detected input below to replace automatic activations."))
            )
            nodes.append(self.candidates)
        nodes.append(
            sl.action_controls(
                sl.action_control(tr(t"video-render = Render video"), self._apply, key="render"),
                sl.action_control(tr(t"video-cancel = Cancel"), self._cancel, key="cancel"),
                key="video-actions",
            )
        )
        return tuple(nodes)

    def _input_detail(self, position: VideoPosition) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.action_controls(
            sl.action_control(
                tr(t"video-use-input = Use this input"), partial(self._use_input, position), key="use-input"
            ),
            key="input-actions",
        )

    async def _use_input(self, position: VideoPosition, _event: sd.PressEvent) -> None:
        self.interactions = tuple(
            item.model_copy(update={"position": position}) if item.position is None else item
            for item in self.interactions
        )

    async def _settings(self, event: sd.SubmitEvent) -> None:
        self.seconds = cast(int, event.values["seconds"])
        self.orbit = cast(float, event.values["orbit"])
        self.simulate = cast(bool, event.values["simulate"])

    async def _add(self, event: sd.SubmitEvent) -> None:
        coordinates = tuple(event.values.get(axis) for axis in ("x", "y", "z"))
        if any(value is not None for value in coordinates) and any(value is None for value in coordinates):
            await event.notice(
                tr(t"video-coordinates-required = Enter all three coordinates, or leave all three blank.")
            )
            return
        if len(self.interactions) >= 16:
            await event.notice(tr(t"video-too-many-activations = A video can contain at most 16 activations."))
            return
        item = VideoInteraction(
            tick=cast(int, event.values["tick"]),
            position=None if coordinates[0] is None else cast(VideoPosition, coordinates),
        )
        self.interactions = tuple(sorted((*self.interactions, item), key=lambda item: item.tick))

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self.interactions = (*self.interactions[:index], *self.interactions[index + 1 :])

    async def _apply(self, event: sd.PressEvent) -> None:
        try:
            recipe = VideoRenderRequest.model_validate(
                self.recipe.model_dump()
                | {
                    "duration_ticks": self.seconds * 20,
                    "orbit_degrees": self.orbit,
                    "simulation_enabled": self.simulate,
                    "interactions": self.interactions if self.simulate else (),
                }
            )
        except ValidationError:
            await event.notice(
                tr(
                    t"video-activation-outside-clip = Every activation must be before the end of the clip. Increase the duration or remove late activations."
                )
            )
            return
        if self.submit is not None:
            await self.submit(event, recipe)
        self.complete(event, recipe)

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)

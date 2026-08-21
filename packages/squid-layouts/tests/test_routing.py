"""Stateless routed controls: ids, dispatch, and drawing without a session."""

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Router, render_static
from squid_layouts.discord.routing import _dispatch_item
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.primitives import Panel, RoutedButton, Row
from squid_layouts.scene.model import SceneRoutedButton, SceneRow

EDIT_BUILD = sl.Route("edit:build:{build_id:int}")
POLL_CLOSE = sl.Route("poll:close")
NAME_BUILD = sl.Route("name:build:{slug}")


class TestRouteFormats:
    def test_ids_and_matches_are_derived_from_one_format(self) -> None:
        assert EDIT_BUILD.params == ("build_id",)
        assert EDIT_BUILD.id(build_id=5) == "edit:build:5"
        assert EDIT_BUILD.match("edit:build:5") == {"build_id": 5}
        assert EDIT_BUILD.match("poll:close") is None

    def test_a_route_with_no_parameters_is_its_own_id(self) -> None:
        assert POLL_CLOSE.id() == "poll:close"
        assert POLL_CLOSE.match("poll:close") == {}

    def test_a_converter_narrows_the_pattern_and_types_the_value(self) -> None:
        # The regression this closes: `edit:build:(\d+)` became `[^:]+` when the hand-rolled
        # DynamicItem moved onto a route, so a non-numeric id reached the handler and blew up
        # in `int()` instead of simply not matching.
        assert EDIT_BUILD.match("edit:build:abc") is None
        assert NAME_BUILD.match("name:build:abc") == {"slug": "abc"}
        assert isinstance(EDIT_BUILD.match("edit:build:5")["build_id"], int)  # type: ignore[index]

    @pytest.mark.parametrize(
        ("params", "match"),
        [
            ({}, "missing"),
            ({"build_id": 5, "extra": 1}, "unknown"),
            ({"build_id": ""}, "cannot match the id it built"),
            ({"build_id": "a:b"}, "cannot match the id it built"),
            ({"build_id": "abc"}, "cannot match the id it built"),
        ],
    )
    def test_an_id_this_route_could_not_match_back_is_refused(self, params: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            EDIT_BUILD.id(**params)

    def test_an_over_budget_id_is_a_layout_invariant_not_a_bad_argument(self) -> None:
        # Distinct from the cases above: the value is a fine `int`, Discord's limit is what
        # rejects it, so it fails as a layout invariant and not as a call error.
        with pytest.raises(LayoutInvariantError, match="over the 100"):
            EDIT_BUILD.id(build_id=10**200)

    @pytest.mark.parametrize(
        ("fmt", "match"),
        [
            ("", "non-empty"),
            ("a:{x}:{x}", "more than once"),
            ("a:{1x}", "not a usable parameter name"),
            ("a:{x:>4}", "unknown converter"),
            ("a:{x:float}", "unknown converter"),
            ("a:{x!r}", "may not carry a conversion"),
        ],
    )
    def test_unusable_formats_are_rejected(self, fmt: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            sl.Route(fmt)


class TestRouter:
    async def test_a_handler_takes_its_route_parameters_by_name(self) -> None:
        seen: list[int] = []
        router = Router()

        @router.route(EDIT_BUILD)
        async def edit(_interaction, build_id: int) -> None:
            seen.append(build_id)

        await router.dispatch(None, "edit:build:42")  # type: ignore[arg-type]
        assert seen == [42]

    async def test_a_handler_may_ignore_parameters_it_does_not_need(self) -> None:
        seen: list[str] = []
        router = Router()

        @router.route(EDIT_BUILD)
        async def edit(_interaction) -> None:
            seen.append("called")

        await router.dispatch(None, "edit:build:42")  # type: ignore[arg-type]
        assert seen == ["called"]

    async def test_a_handler_taking_kwargs_receives_every_parameter(self) -> None:
        seen: list[dict[str, object]] = []
        router = Router()

        @router.route(EDIT_BUILD)
        async def edit(_interaction, **params) -> None:
            seen.append(params)

        await router.dispatch(None, "edit:build:42")  # type: ignore[arg-type]
        assert seen == [{"build_id": 42}]

    def test_a_handler_asking_for_a_parameter_the_route_lacks_fails_at_import(self) -> None:
        # The typo that would otherwise surface as a failed click in production.
        router = Router()

        with pytest.raises(ValueError, match=r"asks for \['biuld_id'\]"):

            @router.route(EDIT_BUILD)
            async def edit(_interaction, biuld_id: int) -> None: ...

    async def test_a_route_may_be_spelled_inline_as_its_format_string(self) -> None:
        # Naming a `Route` only pays when something outside this module builds ids from it.
        seen: list[int] = []
        router = Router()

        @router.route("edit:build:{build_id:int}")
        async def edit(_interaction, build_id: int) -> None:
            seen.append(build_id)

        await router.dispatch(None, "edit:build:7")  # type: ignore[arg-type]
        assert seen == [7]

    def test_a_handler_taking_no_arguments_at_all_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must take the interaction"):
            Router().add(POLL_CLOSE, lambda: None)  # type: ignore[arg-type]

    async def test_a_retired_route_is_logged_rather_than_raised(self) -> None:
        # Buttons outlive the code that answered them; an unknown id must not crash dispatch.
        await Router().dispatch(None, "gone:forever")  # type: ignore[arg-type]

    async def test_a_failing_handler_reaches_the_error_hook(self) -> None:
        seen: list[str] = []

        async def hook(interaction, error, source: str) -> None:
            seen.append(source)

        router = Router(on_error=hook)

        @router.route(POLL_CLOSE)
        async def close(_interaction) -> None:
            raise RuntimeError("boom")

        await router.dispatch(None, "poll:close")  # type: ignore[arg-type]
        assert seen == ["route:poll:close"]

    def test_one_template_covers_every_route_so_a_click_dispatches_once(self) -> None:
        router = Router()
        router.add(POLL_CLOSE, _noop)
        router.add(EDIT_BUILD, _noop)

        template = router.template()
        assert template.fullmatch("poll:close")
        assert template.fullmatch("edit:build:9")
        assert not template.fullmatch("something:else")
        assert "(?P<" not in template.pattern  # groups would collide across routes

    def test_an_empty_router_matches_nothing(self) -> None:
        assert not Router().template().fullmatch("anything")

    def test_shadowing_routes_are_rejected(self) -> None:
        router = Router()
        router.add(POLL_CLOSE, _noop)

        with pytest.raises(ValueError, match="overlaps"):
            router.add(sl.Route("poll:{action}"), _noop)

    async def test_re_registering_a_route_replaces_its_handler(self) -> None:
        # Loading an extension re-executes its module, so a reload registers again.
        seen: list[str] = []
        router = Router()
        router.add(POLL_CLOSE, _noop)

        async def replacement(_interaction) -> None:
            seen.append("replacement")

        router.add(sl.Route("poll:close"), replacement)
        await router.dispatch(None, "poll:close")  # type: ignore[arg-type]

        assert seen == ["replacement"]
        assert router.template().pattern == "(?:poll:close)"

    async def test_a_reload_still_works_once_the_template_is_built(self) -> None:
        router = Router()
        router.add(POLL_CLOSE, _noop)
        router._registered = True

        router.add(sl.Route("poll:close"), _noop)  # a reload leaves the template unchanged
        with pytest.raises(RuntimeError, match=r"before Router\.register"):
            router.add(EDIT_BUILD, _noop)

    def test_the_generated_dispatch_item_accepts_its_own_ids(self) -> None:
        router = Router()
        router.add(EDIT_BUILD, _noop)

        # DynamicItem's own constructor validates the id against the template.
        item = _dispatch_item(router)(discord.ui.Button(label="Edit", custom_id="edit:build:7"))

        assert item.custom_id == "edit:build:7"


class TestDrawing:
    def test_a_sessionless_document_may_carry_a_routed_control(self) -> None:
        document = sl.section(
            sl.actions(sl.routed_action("Close poll", POLL_CLOSE.id(), key="close", tone=sl.Tone.DANGER), key="c"),
            heading="Poll",
        )

        view = render_static([document])
        buttons = [child for child in view.walk_children() if isinstance(child, discord.ui.Button)]

        assert [(button.custom_id, button.style) for button in buttons] == [("poll:close", discord.ButtonStyle.danger)]

    def test_a_routed_button_places_exactly_where_a_primitive_asks(self) -> None:
        document = Panel((Row((RoutedButton("Edit", EDIT_BUILD.id(build_id=3)),)),))

        view = render_static([document])
        buttons = [child for child in view.walk_children() if isinstance(child, discord.ui.Button)]

        assert [button.custom_id for button in buttons] == ["edit:build:3"]

    def test_a_bound_control_still_needs_a_session(self) -> None:
        document = sl.actions(sl.action("Press", _press, key="press"), key="c")

        with pytest.raises(TypeError, match="require a mounted Discord frontend"):
            render_static([document])

    def test_a_routed_scene_round_trips_through_the_codec(self) -> None:
        document = sl.section(sl.actions(sl.routed_action("Edit", EDIT_BUILD.id(build_id=3), key="e"), key="c"))

        scene = sl.plan(document, target=sl.discord.DEFAULT_TARGET).scene
        payload = sl.scene.Codec.dumps(scene)

        assert "routed_button" in sl.scene.Codec.schema()["$defs"]
        assert sl.scene.Codec.loads(payload) == scene
        row = scene.children[0].children[0]  # type: ignore[union-attr]
        assert isinstance(row, SceneRow)
        assert row.items == (SceneRoutedButton("Edit", "edit:build:3"),)

    def test_the_html_preview_emits_the_route(self) -> None:
        document = sl.actions(sl.routed_action("Close", POLL_CLOSE.id(), key="close"), key="c")

        html = sl.html.Renderer().draw(sl.plan(document, target=sl.discord.DEFAULT_TARGET).scene)

        assert 'data-squid-route="poll:close"' in html


async def _noop(_interaction) -> None: ...


async def _press(_event) -> None: ...

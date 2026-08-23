"""Squid regions contributed to a host-owned Components V2 view."""

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import (
    V2_LIMITS as LIMITS,
)
from squid_layouts.discord import ExistingLayoutError, ResourceCost, contribute
from squid_layouts.discord.fragments import FragmentOwnershipError, StaleReservationError, fragment
from squid_layouts.discord.inspection import audit


def _host(*items: discord.ui.Item) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    for item in items:
        view.add_item(item)
    return view


def _row(*items) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


def _text(content: str) -> list:
    return [sl.primitives.Text(content)]


class TestContribute:
    def test_places_the_fragment_then_the_trailing_items(self):
        host = _host(discord.ui.TextDisplay("header"))
        controls = _row(discord.ui.Button(label="ok", custom_id="ok"))
        contribute(_text("body"), to=host, followed_by=(controls,))
        kinds = [type(child).__name__ for child in host.children]
        assert kinds == ["TextDisplay", "TextDisplay", "ActionRow"]
        assert host.children[2] is controls

    def test_the_validated_view_is_the_final_view(self):
        # The whole point of followed_by: the trailing row is costed before anything moves,
        # so what preflight proved legal is what the host will send.
        host = _host(discord.ui.TextDisplay("h" * 1000))
        controls = _row(*(discord.ui.Button(label="b", custom_id=f"b{index}") for index in range(5)))
        contribute(_text("x" * 5000), to=host, followed_by=(controls,))
        assert audit(host).ok
        assert host.content_length() <= LIMITS.total_text

    def test_the_fragment_shrinks_for_items_that_do_not_exist_yet(self):
        host = _host(discord.ui.TextDisplay("h" * 1000))
        trailing = discord.ui.TextDisplay("t" * 500)
        contribute(_text("x" * 5000), to=host, followed_by=(trailing,))
        assert host.content_length() <= LIMITS.total_text

    def test_reserve_covers_resources_no_item_describes(self):
        host = _host()
        contribute(_text("x" * 5000), to=host, reserve=ResourceCost({"display_text": 3000}))
        assert host.content_length() <= LIMITS.total_text - 3000

    def test_host_items_are_never_touched(self):
        header = discord.ui.TextDisplay("header")
        host = _host(header)
        contribute(_text("body"), to=host)
        assert host.children[0] is header
        assert header.content == "header"

    def test_degradation_is_reported_through(self):
        host = _host(discord.ui.TextDisplay("h" * 3000))
        attached = contribute(_text("x" * 5000), to=host)
        assert attached.report.events


class TestPreflight:
    def test_an_already_invalid_host_is_refused_before_planning(self):
        host = _host(discord.ui.TextDisplay("x" * (LIMITS.total_text + 1)))
        with pytest.raises(ExistingLayoutError):
            contribute(_text("body"), to=host)

    def test_duplicate_custom_ids_fail_before_mutation(self):
        host = _host(_row(discord.ui.Button(label="a", custom_id="dup")))
        before = list(host.children)
        with pytest.raises(ExistingLayoutError, match="already used"):
            contribute(_text("body"), to=host, followed_by=(_row(discord.ui.Button(label="b", custom_id="dup")),))
        assert list(host.children) == before

    def test_attachment_capacity_counts_host_and_fragment(self):
        host = _host()
        with pytest.raises(ExistingLayoutError, match="attachments"):
            contribute(_text("body"), to=host, attachments=LIMITS.attachments + 1)

    def test_a_changed_host_invalidates_the_plan(self):
        host = _host(discord.ui.TextDisplay("header"))
        planned = fragment(_text("body"), alongside=host)
        host.add_item(discord.ui.TextDisplay("surprise"))
        with pytest.raises(StaleReservationError):
            planned.attach(host)

    def test_an_item_owned_by_another_view_is_refused(self):
        other = _host()
        trailing = discord.ui.TextDisplay("elsewhere")
        other.add_item(trailing)
        with pytest.raises(FragmentOwnershipError, match="another view"):
            contribute(_text("body"), to=_host(), followed_by=(trailing,))

    def test_rollback_leaves_the_host_untouched_and_the_fragment_usable(self):
        class Hostile(discord.ui.LayoutView):
            def __init__(self):
                super().__init__(timeout=None)
                self.armed = False

            def add_item(self, item):
                if self.armed:
                    message = "no"
                    raise RuntimeError(message)
                return super().add_item(item)

        host = Hostile()
        host.add_item(discord.ui.TextDisplay("header"))
        planned = fragment(_text("body"), alongside=host)
        host.armed = True
        with pytest.raises(RuntimeError):
            planned.attach(host)
        assert [type(child).__name__ for child in host.children] == ["TextDisplay"]

        host.armed = False
        attached = planned.attach(host)
        assert len(host.children) == 2
        assert attached.items


class TestInteractionBoundary:
    def test_routed_controls_are_allowed(self):
        host = _host()
        routed = sl.discord.renderer.RoutedItem(label="go", custom_id="r:go")
        contribute(_text("body"), to=host, followed_by=(_row(routed),))
        assert routed in list(host.walk_children())

    def test_a_dispatchable_native_item_is_refused(self):
        native = sl.discord.targets.NativeItem(
            lambda: _row(discord.ui.Button(label="local", custom_id="local")),
            fallback=sl.primitives.Text("fallback"),
        )
        with pytest.raises(FragmentOwnershipError, match="does not own"):
            fragment([native], alongside=_host())

    def test_a_native_display_item_is_fine(self):
        native = sl.discord.targets.NativeItem(
            lambda: discord.ui.TextDisplay("native"),
            fallback=sl.primitives.Text("fallback"),
        )
        planned = fragment([native], alongside=_host())
        assert planned.items


class TestAttachedFragment:
    def test_removal_is_identity_based(self):
        host = _host(discord.ui.TextDisplay("header"))
        attached = contribute(_text("body"), to=host)
        # A host replacement carrying the same content must survive removal.
        host.add_item(discord.ui.TextDisplay("body"))
        attached.remove()
        assert [child.content for child in host.children if isinstance(child, discord.ui.TextDisplay)] == [
            "header",
            "body",
        ]

    def test_files_are_repeatable_and_fresh(self):
        host = _host()
        attached = contribute(_text("body"), to=host)
        assert attached.files() == []
        assert attached.attachments([]) == []

    def test_attaching_twice_is_refused(self):
        planned = fragment(_text("body"), alongside=_host())
        planned.attach(_host())
        with pytest.raises(FragmentOwnershipError, match="already been placed"):
            planned.attach(_host())

    def test_staleness_is_observable_after_the_fact(self):
        host = _host()
        attached = contribute(_text("body"), to=host)
        assert not attached.stale()
        host.add_item(discord.ui.TextDisplay("host moved on"))
        assert attached.stale()


class TestRelease:
    def test_hands_over_items_for_manual_placement(self):
        planned = fragment(_text("body"), alongside=_host())
        items = planned.release()
        host = _host()
        host.add_item(discord.ui.TextDisplay("header"))
        for item in items:
            host.add_item(item)
        assert len(host.children) == 2

    def test_released_fragments_cannot_also_be_attached(self):
        planned = fragment(_text("body"), alongside=_host())
        planned.release()
        with pytest.raises(FragmentOwnershipError):
            planned.attach(_host())


def test_a_fragment_never_exceeds_whole_document_planning():
    """The combined result must obey the same limits as composing everything at once."""
    body = "x" * 6000
    whole = sl.discord.compose([sl.primitives.Text("header"), sl.primitives.Text(body)]).view

    host = _host(discord.ui.TextDisplay("header"))
    contribute([sl.primitives.Text(body)], to=host)

    assert host.content_length() <= whole.content_length()
    assert len(list(host.walk_children())) <= LIMITS.total_components
    assert audit(host).ok

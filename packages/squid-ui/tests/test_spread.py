"""A document that exceeds one message is cut into consecutive messages."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid_ui.errors import UnsolvableLayoutError
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import Axis
from squid_ui.planning.spread import plan_spread
from squid_ui.presentation_state import ActivePagers, RegionLayoutUpdate, RetainedCompositionUpdate

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "spread.discord", ">=1"))
# Each paragraph costs one component, so four fit in a message.
REQUEST = PlanRequest(target=replace(V2, limits=replace(V2.limits, total_components=4)))


def parts(count: int) -> tuple[sl.semantic.Paragraph, ...]:
    return tuple(sl.paragraph(f"Part {index}", key=f"p{index}") for index in range(count))


def components(spread: sl.planning.MessageSpread) -> list[int]:
    return [message.cost.get(Axis.COMPONENTS) for message in spread.messages]


def test_a_document_that_fits_stays_one_message() -> None:
    spread = plan_spread(parts(4), REQUEST, max_messages=3)

    assert components(spread) == [4]
    assert spread.anchors == ()


def test_an_oversized_document_uses_the_fewest_messages_and_balances_them() -> None:
    spread = plan_spread(parts(6), REQUEST, max_messages=3)

    # Filling the first message would give 4 and 2; the balanced cut leaves both room.
    assert components(spread) == [3, 3]
    assert spread.anchors == ("p3",)


def test_the_single_message_error_is_raised_when_no_spread_is_allowed_or_possible() -> None:
    with pytest.raises(UnsolvableLayoutError):
        plan_spread(parts(6), REQUEST, max_messages=1)
    with pytest.raises(UnsolvableLayoutError):
        plan_spread(parts(9), REQUEST, max_messages=2)


def test_no_cut_follows_a_child_kept_with_its_successor() -> None:
    first, second, third, *rest = parts(6)

    spread = plan_spread((first, second, sl.keep_with_next(third), *rest), REQUEST, max_messages=3)

    assert spread.anchors != ("p3",)
    assert components(spread) in ([4, 2], [2, 4])


def test_retained_anchors_keep_a_cut_until_its_child_disappears() -> None:
    nodes = parts(6)
    first = plan_spread(nodes, REQUEST, max_messages=3)

    # Four parts would now fit one message, but the cut before p3 is still usable.
    shrunk = plan_spread((*nodes[:2], nodes[3], nodes[4]), REQUEST, max_messages=3, retained=first.anchors)
    merged = plan_spread(nodes[:3], REQUEST, max_messages=3, retained=first.anchors)

    assert components(shrunk) == [2, 2]
    assert shrunk.anchors == first.anchors
    assert components(merged) == [3]


def test_a_retained_cut_that_no_longer_fits_is_searched_again() -> None:
    nodes = parts(9)
    first = plan_spread(nodes[:6], REQUEST, max_messages=3)

    grown = plan_spread(nodes, REQUEST, max_messages=3, retained=first.anchors)

    assert components(grown) == [3, 3, 3]


def test_writes_about_the_whole_display_are_merged_across_messages() -> None:
    spread = plan_spread(parts(6), REQUEST, max_messages=2)

    assert [type(update) for update in spread.session_updates] == [
        ActivePagers,
        RegionLayoutUpdate,
        RetainedCompositionUpdate,
    ]
    for message in spread.messages:
        assert not any(
            isinstance(update, ActivePagers | RetainedCompositionUpdate) for update in message.session_updates
        )


def test_a_top_level_cut_is_preferred_to_one_inside_a_stack() -> None:
    first, second, third, *rest = parts(5)

    spread = plan_spread((sl.stack(first, second, third), *rest), REQUEST, max_messages=3)

    assert spread.anchors == ("p3",)
    assert components(spread) == [3, 2]


def test_a_stack_too_big_for_one_message_is_cut_inside_and_kept_in_each_part() -> None:
    spread = plan_spread((sl.stack(*parts(6), key="group"),), REQUEST, max_messages=3)

    assert spread.anchors == ("group/p3",)
    assert components(spread) == [3, 3]


# Each list item costs about 22 characters, so four fit in a message with room to spare.
TEXT = PlanRequest(target=replace(V2, limits=replace(V2.limits, total_text=100)))


def items(count: int, **options: object) -> sl.semantic.List:
    return sl.bullets(
        *(sl.bullet(f"Item {index} " + "x" * 13, key=f"i{index}") for index in range(count)),
        key="items",
        group_size=count,
        **options,  # type: ignore[arg-type]
    )


def test_a_fixed_group_too_big_for_one_message_continues_without_paging() -> None:
    spread = plan_spread(items(8), TEXT, max_messages=3)

    assert len(spread.messages) == 2
    assert spread.anchors[0] is not None and spread.anchors[0].startswith("items/")
    assert not any(message.effects for message in spread.messages)


def test_spreading_is_preferred_to_shortening_text() -> None:
    nodes = (sl.truncate(sl.paragraph("y" * 90, key="long")), sl.paragraph("z" * 30, key="short"))

    spread = plan_spread(nodes, TEXT, max_messages=2)
    single = plan_spread(nodes, TEXT, max_messages=1)

    assert spread.anchors == ("short",)
    assert not any(message.effects for message in spread.messages)
    assert single.messages[0].effects


def test_a_fixed_group_that_cannot_fit_fails_unless_it_may_shorten_or_split() -> None:
    with pytest.raises(UnsolvableLayoutError):
        plan_spread(items(8), TEXT, max_messages=1)

    shortened = plan_spread(sl.truncate(items(8)), TEXT, max_messages=1).messages[0]
    split = plan_spread(items(8, split_footer=lambda part, parts: f"{part}/{parts}"), TEXT, max_messages=1)

    assert shortened.effects
    assert not split.messages[0].effects

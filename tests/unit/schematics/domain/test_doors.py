import pytest

from squid.core.schematics.domain.doors.detection import detect_cycle
from squid.core.schematics.domain.models import Vector3

STONE = "minecraft:stone"
GLASS = "minecraft:glass"
FULL = {STONE, GLASS}


def framed(profile: set[tuple[int, int]], depth: int) -> dict[Vector3, str]:
    return {
        (x + dx, y + dy, z): STONE
        for x, y in profile
        for z in range(depth)
        for dx, dy in ((0, 1), (1, 0), (-1, 0), (0, -1))
        if (x + dx, y + dy) not in profile
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_irregular_mixed_panel_and_extra_layer(reverse: bool) -> None:
    profile = {(-2, 5), (-2, 6), (-1, 5)}
    opened = framed(profile, 3)
    panel = {(x, y, z): GLASS if (x + y) % 2 else STONE for x, y in profile for z in (1, 2)}
    closed = opened | panel
    first, second = (closed, opened) if reverse else (opened, closed)
    result = detect_cycle(first, second, first, FULL)
    (candidate,) = result.candidates
    assert candidate.axis == 2
    assert candidate.closed_endpoint == ("initial" if reverse else "actuated")
    assert candidate.seal == "sealed"
    assert candidate.opening_size == (2, 2)
    assert not candidate.agreed_blocks
    assert set(candidate.disputed_blocks) == set(panel)
    assert {m.depth for m in candidate.patterns if m.arrangement == "flat"} == {1, 2}
    assert all("checkerboard" in match.compositions for match in candidate.patterns)


def test_partial_panel_keeps_the_whole_hallway_and_reports_no_pattern() -> None:
    profile = {(x, y) for x in range(3) for y in range(3)}
    opened = framed(profile, 3)
    closed = opened | {(x, y, 1): STONE for x in range(2) for y in range(2)}
    (candidate,) = detect_cycle(opened, closed, opened, FULL).candidates
    assert candidate.opening_size == (3, 3)
    assert candidate.seal == "passage_remains"
    assert not candidate.patterns


def test_funnel_uses_all_depths_and_excludes_remote_sheet() -> None:
    profile = {(x, y) for x in range(5) for y in range(5)}
    opened = framed(profile, 4)
    funnel = {(x, y, min(x, y, 4 - x, 4 - y)): STONE for x, y in profile}
    machinery = {(x + 20, y, 1): GLASS for x, y in profile}
    (candidate,) = detect_cycle(opened, opened | funnel | machinery, opened, FULL).candidates
    assert candidate.patterns[0].arrangement == "stepped_funnel"
    assert set(candidate.agreed_blocks) == set(funnel)


def test_bar_pattern_allows_a_passage_and_partial_blocks_cannot_prove_a_seal() -> None:
    profile = {(x, y) for x in range(3) for y in range(3)}
    opened = framed(profile, 3)
    bar = {(1, y, 1): STONE for y in range(3)}
    (candidate,) = detect_cycle(opened, opened | bar, opened, FULL).candidates
    assert candidate.seal == "passage_remains"
    assert [match.arrangement for match in candidate.patterns] == ["center_bar"]
    partial = {(x, y, 1): "minecraft:oak_slab[type=bottom]" for x, y in profile}
    (candidate,) = detect_cycle(opened, opened | partial, opened, FULL).candidates
    assert candidate.seal == "unknown"
    assert not detect_cycle(opened, opened | partial, opened | partial, FULL).candidates

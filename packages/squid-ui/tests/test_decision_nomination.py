"""Decision nomination descends every container and refuses what it cannot search."""

from typing import Any, cast

import pytest

import squid_ui as sl
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.limits import LIMITS
from squid_ui.planning.semantic_adaptation.decisions import nominate_decisions
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.target_types import DiscordTarget, Renderable


def test_container_interiors_contribute_their_axes() -> None:
    # A forgotten descent arm would not crash; the interior axis would just vanish from the
    # search space. This pins the failure mode the exhaustive walk exists to prevent.
    document = sl.section(
        sl.heading("Report"),
        sl.table(
            sl.columns(sl.column("Name"), sl.column("Score")),
            sl.table_row("Ada", "10"),
            key="scores",
        ),
    )

    decisions = nominate_decisions([document], limits=LIMITS, session=PresentationState())

    assert [axis.path for axis in decisions.strategies] == ["$.0.0"]


def test_unknown_renderables_are_rejected_with_their_path() -> None:
    class Unregistered(Renderable[DiscordTarget]):
        pass

    with pytest.raises(LayoutInvariantError, match=r"\$\.0: Unregistered is neither"):
        nominate_decisions([cast(Any, Unregistered())], limits=LIMITS, session=PresentationState())

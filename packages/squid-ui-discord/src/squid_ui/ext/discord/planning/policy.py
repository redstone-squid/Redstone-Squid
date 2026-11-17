"""Discord-specific realization policy, layered over shared offer constraints."""

from collections.abc import Mapping
from dataclasses import dataclass, fields

from squid_ui.planning.offers.policy import OfferPolicy, OfferScope


@dataclass(frozen=True, slots=True)
class DiscordOfferPolicy(OfferPolicy):
    native_v2: bool = False
    embed_limits: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class DiscordOfferScope[R, C](OfferScope[R, C]):
    policy: DiscordOfferPolicy

    @classmethod
    def from_scope(cls, scope: OfferScope[R, C]) -> DiscordOfferScope[R, C]:
        """Keep shared constraints while supplying native defaults on other targets."""
        policy = scope.policy
        if not isinstance(policy, DiscordOfferPolicy):
            policy = DiscordOfferPolicy(**{item.name: getattr(policy, item.name) for item in fields(OfferPolicy)})
        return cls(
            scope.occurrence, scope.context, policy, scope.text_capacity, scope.keep, scope.spill, scope.inside_panel
        )

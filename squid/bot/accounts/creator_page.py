"""Show somebody's public creator page."""

from typing import Self, override
from uuid import UUID

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.profile_render import public_profile_fields
from squid.bot.follows import follow_control
from squid.bot.routes import AppRequest, AppRoutes, ScreenUnavailable
from squid.bot.ui import card_node, tr
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import PublicCreatorProfile
from squid.core.notifications.domain import SubscriptionKind
from squid_ui.ext.discord.target_types import ComponentsV2Target

router = AppRoutes()


class CreatorPage(sd.Screen):
    """A creator's public profile, rendered from the same filtered view the API serves.

    A hidden page still lists the names its builds are credited to.
    """

    policy = sd.ResponseSpec(audience="personal", access=sd.Everyone(), timeout=300)

    def __init__(self, profile: PublicCreatorProfile, fallback_name: sl.TextLike) -> None:
        self.profile = profile
        self.fallback_name = fallback_name

    @classmethod
    async def load(cls, accounts: AccountService, public_id: UUID, fallback_name: sl.TextLike) -> Self:
        """Load a page, following merge redirects.

        Raises:
            ScreenUnavailable: No creator has this public ID.
        """
        profile = await accounts.get_public_profile(public_id)
        if profile is None:
            raise ScreenUnavailable(
                tr(t"bot-verify-that-creator-page-could-not-be-found = That creator page could not be found.")
            )
        return cls(profile, fallback_name)

    @router.route("/creators/{public_id}")
    @classmethod
    async def open(cls, request: AppRequest, public_id: UUID) -> Self:
        """Open a creator page privately for the requester."""
        return await cls.load(request.client.services.accounts, public_id, tr(t"creator-page-fallback-name = Creator"))

    @property
    def public_id(self) -> UUID:
        """The current public ID, which differs from the requested one after an account merge."""
        return self.profile.canonical_public_id or self.profile.public_id

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        profile = self.profile
        if profile.hidden:
            card = card_node(
                tr(t"bot-verify-hidden-creator-page = Hidden creator page"),
                tr(
                    t"verify-creator-page-hidden = This creator has hidden their "
                    t"page. Their build credit is still listed."
                ),
                fields=public_profile_fields(profile),
            )
        else:
            card = card_node(
                profile.display_name or self.fallback_name,
                profile.bio,
                fields=public_profile_fields(profile),
                media=() if profile.avatar_url is None else (profile.avatar_url,),
            )
        if profile.hidden:
            return (card,)
        return (
            card,
            sl.action_controls(follow_control(SubscriptionKind.CREATOR, self.public_id), key="creator-actions"),
        )

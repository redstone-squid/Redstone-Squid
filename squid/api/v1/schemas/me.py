"""Authenticated self-account representations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from whenever import Instant

from squid.accounts.domain import (
    Account,
    AccountIdentity,
    AccountMerge,
    AccountProfile,
    IdentityProvider,
    IdentityRefresh,
    MergePreview,
    ProfileLink,
    ProfileUpdate,
    avatar_url_for,
)
from squid.accounts.domain.profiles import UNSET


class ProfileLinkDetail(BaseModel):
    """One external link published on a creator profile."""

    model_config = ConfigDict(extra="forbid")

    label: str
    url: str

    @classmethod
    def from_domain(cls, link: ProfileLink) -> ProfileLinkDetail:
        return cls(label=link.label, url=link.url)


class AvatarDetail(BaseModel):
    """The linked identity a profile's avatar is rendered from."""

    model_config = ConfigDict(extra="forbid")

    identity_id: int
    provider: IdentityProvider
    url: str | None
    """Null when the provider cannot supply one yet, such as a Discord identity whose avatar
    hash has not been observed since it was linked."""


class IdentityDetail(BaseModel):
    """One identity linked to the caller's own account.

    Carries the internal `id` because that is the handle every write takes: an account can hold
    two identities from one provider once it has absorbed another account in a merge, so
    "the Discord one" is not a usable address.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    provider: IdentityProvider
    subject: str
    display_name: str | None
    verified_at: Instant | None
    is_public: bool
    """Whether this identity appears on the account's public creator profile."""

    @classmethod
    def from_domain(cls, identity: AccountIdentity) -> IdentityDetail:
        assert identity.id is not None
        return cls(
            id=identity.id,
            provider=identity.provider,
            subject=identity.subject,
            display_name=identity.display_name,
            verified_at=identity.verified_at,
            is_public=identity.is_public,
        )


class ProfileDetail(BaseModel):
    """The caller's own profile, including anything it has chosen to hide."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None
    bio: str | None
    pronouns: str | None
    links: list[ProfileLinkDetail]
    hidden: bool
    """Whether the public creator page is withheld. A hidden profile still serves its aliases and
    build credits, so builds stay attributable."""

    avatar: AvatarDetail | None

    @classmethod
    def from_domain(cls, profile: AccountProfile, identities: tuple[AccountIdentity, ...]) -> ProfileDetail:
        source = next(
            (identity for identity in identities if identity.id == profile.avatar_identity_id),
            None,
        )
        avatar = (
            None
            if source is None or source.id is None
            else AvatarDetail(identity_id=source.id, provider=source.provider, url=avatar_url_for(source))
        )
        return cls(
            display_name=profile.display_name,
            bio=profile.bio,
            pronouns=profile.pronouns,
            links=[ProfileLinkDetail.from_domain(link) for link in profile.links],
            hidden=profile.hidden,
            avatar=avatar,
        )


class UserMe(BaseModel):
    """The caller's own account: who they are, how they sign in, and what they publish.

    The identity list supports any number of Discord, Java, or future provider identities. The
    previous shape flattened one Discord identity and one Java identity into four top-level fields,
    which could not describe a caller with two of either and implied Discord for callers with none.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    creator_id: UUID
    """The public identifier this account's creator page is served under."""

    created_at: Instant | None
    consent_version: str | None
    consent_pending: bool
    identities: list[IdentityDetail]
    profile: ProfileDetail

    @classmethod
    def from_domain(
        cls,
        account: Account,
        profile: AccountProfile,
        *,
        consent_pending: bool,
    ) -> UserMe:
        assert account.id is not None
        assert account.public_creator_id is not None
        return cls(
            id=account.id,
            creator_id=account.public_creator_id,
            created_at=account.created_at,
            consent_version=account.consent.version if account.consent is not None else None,
            consent_pending=consent_pending,
            identities=[IdentityDetail.from_domain(identity) for identity in account.identities],
            profile=ProfileDetail.from_domain(profile, account.identities),
        )


class ProfileUpdateRequest(BaseModel):
    """A partial profile edit.

    Omitting a field leaves it alone; sending `null` clears it. Pydantic cannot express that
    difference in the annotation alone, so `to_domain` reads `model_fields_set` — which is also
    why every field defaults to `None` rather than to a sentinel clients would have to send.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    bio: str | None = None
    pronouns: str | None = None
    links: list[ProfileLinkDetail] | None = None
    hidden: bool | None = None
    avatar_identity_id: int | None = None

    def to_domain(self) -> ProfileUpdate:
        """Build the domain patch, distinguishing an omitted field from an explicit null."""
        present = self.model_fields_set
        return ProfileUpdate(
            display_name=self.display_name if "display_name" in present else UNSET,
            bio=self.bio if "bio" in present else UNSET,
            pronouns=self.pronouns if "pronouns" in present else UNSET,
            links=(
                tuple(ProfileLink(label=link.label, url=link.url) for link in self.links or ())
                if "links" in present
                else UNSET
            ),
            # `hidden` has no meaningful null: clearing it is setting it false.
            hidden=bool(self.hidden) if "hidden" in present else UNSET,
            avatar_identity_id=self.avatar_identity_id if "avatar_identity_id" in present else UNSET,
        )


class IdentityVisibilityRequest(BaseModel):
    """Whether one linked identity appears on the public creator profile."""

    model_config = ConfigDict(extra="forbid")

    public: bool


class MinecraftIdentityRefresh(BaseModel):
    """What re-reading the linked Minecraft name changed."""

    model_config = ConfigDict(extra="forbid")

    minecraft_uuid: str
    ign: str
    previous_ign: str | None
    renamed: bool
    claimed_creator_name: str | None
    """The creator credit now attributed to the caller under the current name."""
    retained_creator_names: tuple[str, ...]
    """Credits kept under previously verified names. A rename does not retract them."""
    contested_creator_name: str | None
    """Set when the current name is credited to a different account and was not taken."""
    pending_claim_id: int | None
    """The staff review opened for a contested name."""

    @classmethod
    def from_domain(cls, refresh: IdentityRefresh) -> MinecraftIdentityRefresh:
        return cls(
            minecraft_uuid=str(refresh.java_uuid),
            ign=refresh.current_name,
            previous_ign=refresh.previous_name,
            renamed=refresh.renamed,
            claimed_creator_name=None if refresh.claimed_alias is None else refresh.claimed_alias.name,
            retained_creator_names=refresh.retained_alias_names,
            contested_creator_name=None if refresh.contested_alias is None else refresh.contested_alias.name,
            pending_claim_id=None if refresh.opened_claim is None else refresh.opened_claim.id,
        )


class MergeCodeDetail(BaseModel):
    """A freshly minted, single-use merge code.

    The plaintext appears here and nowhere else: persistence keeps only a digest, so a lost code
    is reminted rather than recovered.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    expires_at: Instant


class MergeRequest(BaseModel):
    """A merge code, redeemed by the account that will survive."""

    model_config = ConfigDict(extra="forbid")

    code: str


class MergePreviewDetail(BaseModel):
    """What completing a merge would move, shown before the irreversible call."""

    model_config = ConfigDict(extra="forbid")

    absorbed_creator_id: UUID
    """The creator id that would become a permanent redirect to the caller's."""

    alias_names: list[str]
    identity_count: int
    build_count: int

    @classmethod
    def from_domain(cls, preview: MergePreview) -> MergePreviewDetail:
        return cls(
            absorbed_creator_id=preview.absorbed_public_creator_id,
            alias_names=list(preview.alias_names),
            identity_count=preview.identity_count,
            build_count=preview.build_count,
        )


class AccountMergeDetail(BaseModel):
    """The stable identities left behind by a completed merge."""

    model_config = ConfigDict(extra="forbid")

    surviving_creator_id: UUID
    redirected_creator_id: UUID
    """Permanently redirects to `surviving_creator_id`, so links to the absorbed creator survive."""

    @classmethod
    def from_domain(cls, merge: AccountMerge) -> AccountMergeDetail:
        return cls(
            surviving_creator_id=merge.surviving_public_creator_id,
            redirected_creator_id=merge.redirected_public_creator_id,
        )

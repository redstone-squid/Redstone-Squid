"""Compose the read models for browser pages from public data and the current session."""

from dataclasses import dataclass, replace
from decimal import Decimal

from litestar import Request
from pydantic import BaseModel, Field

from squid.api.links import relative_url_for
from squid.api.security import Caller
from squid.api.v1.schemas.builds import BuildDetail, BuildMedia, BuildSchematicOverview, BuildViewer
from squid.api.v1.schemas.schematics import SchematicSummary
from squid.core.accounts.domain.profiles import PublicCreatorProfile
from squid.core.auth.application.viewer import SessionCounts, SessionUser
from squid.core.builds.application.queries import PublicBuildPreview
from squid.core.builds.domain.edit_policy import BuildEditDecision, decide_build_edit
from squid.core.builds.models import Build
from squid.core.notifications.domain import RecordSubscriptionFilter, SubscriptionKind, TagPredicate
from squid.core.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_DOWNLOAD_PRIVATE,
    BUILD_SUBMISSION_CREATE,
    BUILD_SUBMISSION_EDIT,
)
from squid.core.records.application.models import PublicRecordDetail, RecordViewer
from squid.core.tags.domain import RecordOperator, TagAuthority, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.runtime import ApiServices


@dataclass(frozen=True)
class SessionSnapshot:
    user: SessionUser | None
    permissions: tuple[str, ...]
    counts: SessionCounts


@dataclass(frozen=True)
class TagViewer:
    filter_subscription_id: int | None


class TagDetail(BaseModel):
    id: int
    slug: str
    key: str = Field(validation_alias="stable_key")
    name: str = Field(validation_alias="display_name")
    query_name: str | None
    authority: TagAuthority
    kind: TagSemanticKind = Field(validation_alias="semantic_kind")
    value_type: TagValueType
    restriction_type: str | None
    record_operator: RecordOperator | None
    canonical_unit: str | None
    display_unit: str | None = Field(validation_alias="default_display_unit")
    numeric_step: Decimal | None
    viewer: TagViewer | None = None


@dataclass(frozen=True)
class BrowserViews:
    services: ApiServices
    caller: Caller

    @property
    def account_id(self) -> int | None:
        return self.caller.account_id if self.caller.kind == "account" else None

    async def session(self) -> SessionSnapshot:
        if self.account_id is None:
            return SessionSnapshot(user=None, permissions=(), counts=SessionCounts(0, 0, 0, None))
        user, counts, permissions = await self.services.viewer_queries.session(context=self.caller.context())
        return SessionSnapshot(user, permissions, counts)

    async def build(self, build: Build, request: Request) -> BuildDetail:
        publication = await self.services.build_queries.publication(build, context=self.caller.context())
        attachments = await self.services.schematics.list_public_for_build(build.id, context=self.caller.context())
        primary = next((item for item in attachments if item.is_primary), None)
        summary = (
            SchematicSummary.from_domain(
                primary,
                download_url=relative_url_for(
                    request, "build_schematic_content", build_id=build.id, schematic_id=primary.id
                ),
            )
            if primary is not None
            else None
        )
        detail = replace(
            BuildDetail.from_domain(build),
            approved_at=publication.approved_at,
            submitted_by=publication.submitted_by,
            last_edited=publication.last_edited,
            history_count=publication.history_count,
            vote_session_id=publication.vote_session_id,
            schematics=BuildSchematicOverview(count=len(attachments), primary=summary),
        )
        if self.account_id is not None:
            follows, ballot = await self.services.viewer_queries.build_state(build, context=self.caller.context())
            permissions = await self.services.permissions.check(
                self.caller.to_actor(), BUILD_SUBMISSION_CREATE, BUILD_SUBMISSION_EDIT, BUILD_SCHEMATIC_DOWNLOAD_PRIVATE
            )
            allowed = permissions.allowed_nodes
            can_edit = (
                BUILD_SUBMISSION_CREATE.name in allowed
                and decide_build_edit(
                    set(),
                    status=build.submission_status,
                    is_creator=build.is_creator(self.account_id),
                    is_moderator=BUILD_SUBMISSION_EDIT.name in allowed,
                )
                is not BuildEditDecision.DENY
            )
            # This route currently serves only approved builds.
            detail = replace(
                detail,
                viewer=BuildViewer(
                    can_edit=can_edit,
                    can_review=False,
                    can_download_private_schematics=BUILD_SCHEMATIC_DOWNLOAD_PRIVATE.name in allowed,
                    followed_account_subscription_ids=follows,
                    ballot=ballot,
                ),
            )
        if media := self.services.published_build_media:
            retained = await media.gallery(build.id, context=self.caller.context())
            gallery = [
                BuildMedia(kind=item.kind, url=item.url, width=item.width, height=item.height) for item in retained
            ]
            preview = detail.preview
            mirrors = {item.source_url: item.url for item in retained if item.source_url}
            if preview and preview.kind == "image" and preview.url in mirrors:
                preview = replace(preview, url=mirrors[preview.url])
            if preview is None:
                first = next((item for item in retained if item.kind == "image"), None)
                if first is not None:
                    preview = PublicBuildPreview("image", first.url, first.width, first.height)
            detail = replace(
                detail,
                media=[*gallery, *(item for item in detail.media if item.kind != "image" or item.url not in mirrors)],
                preview=preview,
            )
        return detail

    async def record(self, detail: PublicRecordDetail) -> PublicRecordDetail:
        if self.account_id is None:
            return detail
        sub = await self.services.viewer_queries.subscription(
            SubscriptionKind.RECORD, detail.record_id, context=self.caller.context()
        )
        return replace(detail, viewer=RecordViewer(sub))

    async def creator(self, profile: PublicCreatorProfile) -> PublicCreatorProfile:
        if self.account_id is None:
            return profile
        canonical = profile.canonical_public_id or profile.public_id
        viewer = await self.services.viewer_queries.creator(canonical, context=self.caller.context())
        return replace(profile, viewer=viewer)

    async def tag(self, definition: TagDefinition) -> TagDetail:
        detail = TagDetail.model_validate(definition, from_attributes=True)
        if self.account_id is not None:
            target = RecordSubscriptionFilter(tags=(TagPredicate(definition.id),))
            sub = await self.services.viewer_queries.subscription(
                SubscriptionKind.RECORD_FILTER, target, context=self.caller.context()
            )
            detail.viewer = TagViewer(sub)
        return detail

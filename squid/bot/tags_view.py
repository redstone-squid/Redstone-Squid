"""Canonical tag catalogue, contribution, and moderation workspace."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, cast

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import L
from squid.builds.errors import AliasAlreadyAddedError
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)
from squid.tags.domain import TagDefinition, TagValueType


class TagOperations(Protocol):
    """Tag reads and mutations exposed by the workspace."""

    async def public_definitions(self) -> Sequence[TagDefinition]: ...

    async def pending(self) -> Sequence[TagDefinition]: ...

    async def propose_showcase(
        self,
        display_name: str,
        *,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition: ...

    async def assign_showcase(
        self,
        build_id: int,
        tag_id: int,
        raw_value: str | None,
        *,
        actor_account_id: int,
    ) -> TagDefinition: ...

    async def approve(self, tag_id: int) -> TagDefinition: ...

    async def reject(self, tag_id: int) -> TagDefinition: ...

    async def archive(self, tag_id: int) -> TagDefinition: ...


class RestrictionOperations(Protocol):
    """Restriction taxonomy mutation exposed by the workspace."""

    async def add_alias(self, restriction: str, alias: str) -> None: ...


type TagAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type ModerationRequest = Callable[[sl.PressEvent, TagDefinition, str], Awaitable[None]]


class _ModerationActions(sl.Component[sl.ComponentsV2Target]):
    """Portable actions for one tag detail."""

    def __init__(
        self,
        tag: TagDefinition,
        actions: Sequence[str],
        request: ModerationRequest,
    ) -> None:
        self._tag = tag
        self._actions = tuple(actions)
        self._request = request

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            _tag_fields(self._tag),
            sl.action_controls(
                *(
                    sl.action_control(
                        _action_label(action),
                        lambda event, selected=action: self._request(event, self._tag, selected),
                        key=action,
                        tone=sl.Tone.DANGER if action in {"reject", "archive"} else sl.Tone.SUCCESS,
                    )
                    for action in self._actions
                ),
                key="moderation-actions",
            ),
        )


class TagsScreen(sd.Screen):
    """A tag workspace that ends when closed, replaced, or timed out."""

    session_name = "tags"
    timeout = 300
    visibility = "personal"

    def __init__(
        self,
        tags: TagOperations,
        restrictions: RestrictionOperations,
        *,
        build_id: int | None,
        actor_account_id: int | None,
        capabilities: frozenset[PermissionNode],
        authorize: TagAuthorizer,
    ) -> None:
        self._tags = tags
        self._restrictions = restrictions
        self._build_id = build_id
        self._actor_account_id = actor_account_id
        self._capabilities = capabilities
        self._authorize = authorize
        self._catalogue: sp.Browser[TagDefinition, sl.ComponentsV2Target] | None = None
        self._pending: sp.Browser[TagDefinition, sl.ComponentsV2Target] | None = None
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target] | None = None
        self._decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target] | None = None
        self._pending_action: tuple[TagDefinition, str] | None = None

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        definitions = tuple(await self._tags.public_definitions())
        self._catalogue = sp.Browser(
            sl.sources.list_source(definitions),
            key="tag-catalogue",
            identity=lambda tag: str(tag.id),
            label=lambda tag: tag.display_name,
            summary=_tag_summary,
            detail=(
                lambda tag: _ModerationActions(tag, ("archive",), self._request_moderation)
                if TAG_PROPOSAL_ARCHIVE in self._capabilities
                else _tag_fields(tag)
            ),
            page_size=15,
            title=L(t"Published build tags"),
            empty=L(t"No public build tags are available."),
        )
        if TAG_PROPOSAL_LIST in self._capabilities:
            pending = tuple(await self._tags.pending())
            actions = tuple(
                action
                for action, node in (
                    ("approve", TAG_PROPOSAL_APPROVE),
                    ("reject", TAG_PROPOSAL_REJECT),
                )
                if node in self._capabilities
            )
            self._pending = sp.Browser(
                sl.sources.list_source(pending),
                key="pending-tags",
                identity=lambda tag: str(tag.id),
                label=lambda tag: tag.display_name,
                summary=_tag_summary,
                detail=lambda tag: _ModerationActions(tag, actions, self._request_moderation)
                if actions
                else _tag_fields(tag),
                page_size=15,
                title=L(t"Tag proposals awaiting review"),
                empty=L(t"No tag proposals are awaiting review."),
            )
        self._build_tabs()

    def _build_tabs(self) -> None:
        assert self._catalogue is not None
        tabs = [sp.Tab("catalogue", L(t"Catalogue"), self._catalogue)]
        tabs.append(sp.Tab("contribute", L(t"Propose and apply"), self._contribution_nodes()))
        if self._pending is not None:
            tabs.append(sp.Tab("moderation", L(t"Moderation"), self._pending))
        if RESTRICTION_ALIAS_CREATE in self._capabilities:
            tabs.append(sp.Tab("aliases", L(t"Restriction aliases"), self._alias_nodes()))
        self._tabs = sp.Tabs(tabs, key="tag-tabs", title=L(t"Build tags")).build_component()

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._pending_action is not None and self._decision is not None:
            tag, action = self._pending_action
            tag_name = tag.display_name
            action_label = _action_label(action)
            return (
                sl.section(
                    sl.heading(L(t"Confirm tag moderation")),
                    sl.paragraph(L(t"{action_label} **{tag_name}**?")),
                ),
                self.boundary(self._decision, key="moderation-decision"),
            )
        if self._tabs is None:
            return (sl.status(L(t"Loading build tags.")),)
        return (
            self.boundary(self._tabs, key="tabs"),
            sl.action_controls(sl.action_control(L(t"Close"), self._close, key="close"), key="tag-actions"),
        )

    def _contribution_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._actor_account_id is None:
            return (
                sl.note(L(t"Link and consent an account in `/account` before proposing or applying tags.")),
            )
        build_field: tuple[sl.forms.FormField[Any] | sl.forms.FormText, ...] = (
            ()
            if self._build_id is not None
            else (sl.forms.IntField(key="build_id", label=L(t"Build ID"), minimum=1),)
        )
        return (
            sl.form(L(t"Propose tag"), self._proposal_form(), key="propose", on_submit=self._propose),
            sl.form(
                L(t"Apply tag to build"),
                sl.forms.FormSpec(
                    L(t"Apply showcase tag"),
                    (*build_field, sl.forms.IntField(key="tag_id", label=L(t"Tag ID"), minimum=1),
                     sl.forms.TextField(key="value", label=L(t"Value"), required=False, maximum=300)),
                ),
                key="apply",
                on_submit=self._apply,
            ),
        )

    @staticmethod
    def _proposal_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            L(t"Propose showcase tag"),
            (
                sl.forms.TextField(key="name", label=L(t"Display name"), maximum=80),
                sl.forms.ChoiceField(
                    key="value_type",
                    label=L(t"Value type"),
                    default=TagValueType.NONE,
                    options=tuple(
                        sl.forms.ChoiceOption(kind.value, kind.value.title(), kind) for kind in TagValueType
                    ),
                ),
                sl.forms.TextField(key="query_name", label=L(t"Query field"), required=False, maximum=64),
            ),
        )

    def _alias_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                L(t"Add restriction alias"),
                sl.forms.FormSpec(
                    L(t"Add restriction alias"),
                    (
                        sl.forms.TextField(key="restriction", label=L(t"Canonical restriction"), maximum=100),
                        sl.forms.TextField(key="alias", label=L(t"New alias"), maximum=100),
                    ),
                ),
                key="restriction-alias",
                on_submit=self._add_alias,
            ),
        )

    async def _propose(self, event: sl.SubmitEvent) -> None:
        account_id = self._actor_account_id
        if account_id is None:
            await event.notice(L(t"A linked, consented account is required."))
            return
        name = cast(str, event.values["name"])
        value_type = cast(TagValueType, event.values["value_type"])
        query_name = cast(str | None, event.values.get("query_name"))
        tag = await self._tags.propose_showcase(
            name,
            value_type=value_type,
            query_name=query_name,
            created_by_account_id=account_id,
        )
        tag_id = tag.id
        await self._refresh()
        await event.notice(L(t"Tag #{tag_id} is awaiting staff approval."))

    async def _apply(self, event: sl.SubmitEvent) -> None:
        account_id = self._actor_account_id
        if account_id is None:
            await event.notice(L(t"A linked, consented account is required."))
            return
        build_id = self._build_id or cast(int, event.values["build_id"])
        tag_id = cast(int, event.values["tag_id"])
        value = cast(str | None, event.values.get("value"))
        tag = await self._tags.assign_showcase(build_id, tag_id, value, actor_account_id=account_id)
        tag_name = tag.display_name
        await event.notice(L(t"Attached **{tag_name}** to build #{build_id}."))

    async def _add_alias(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, RESTRICTION_ALIAS_CREATE):
            return
        restriction = cast(str, event.values["restriction"])
        alias = cast(str, event.values["alias"])
        try:
            await self._restrictions.add_alias(restriction, alias)
        except AliasAlreadyAddedError:
            await event.notice(L(t"That alias is already on this restriction."))
            return
        await event.notice(L(t"Restriction alias added."))

    async def _request_moderation(
        self,
        _event: sl.PressEvent,
        tag: TagDefinition,
        action: str,
    ) -> None:
        self._pending_action = (tag, action)
        self._decision = sp.Decision[sl.ComponentsV2Target](
            L(t"This changes the public tag catalogue."),
            (
                sp.DecisionOption("confirm", L(t"Confirm"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", L(t"Cancel")),
            ),
            key="tag-moderation",
        ).build_component(on_decide=self._decide)

    async def _decide(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        pending = self._pending_action
        if pending is None or choice == "cancel":
            self._pending_action = None
            self._decision = None
            return
        tag, action = pending
        node = {
            "approve": TAG_PROPOSAL_APPROVE,
            "reject": TAG_PROPOSAL_REJECT,
            "archive": TAG_PROPOSAL_ARCHIVE,
        }[action]
        if not await self._may(event.source, node):
            self._pending_action = None
            self._decision = None
            return
        mutation = {"approve": self._tags.approve, "reject": self._tags.reject, "archive": self._tags.archive}[action]
        changed = await mutation(tag.id)
        tag_name = changed.display_name
        action_label = _action_label(action)
        self._pending_action = None
        self._decision = None
        await self._refresh()
        await event.source.notice(L(t"{action_label} **{tag_name}**."))

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(L(t"You are no longer allowed to perform this tag operation."))
        return False

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


def _tag_summary(tag: TagDefinition) -> str:
    return f"{tag.display_name} · {tag.semantic_kind.value} · {tag.value_type.value}"


def _tag_fields(tag: TagDefinition) -> sl.semantic.Fields:
    return sl.fields(
        sl.field(L(t"ID"), str(tag.id)),
        sl.field(L(t"Kind"), tag.semantic_kind.value),
        sl.field(L(t"Value"), tag.value_type.value),
        sl.field(L(t"Query field"), tag.query_name or "—"),
    )


def _action_label(action: str) -> sl.TextLike:
    return {"approve": L(t"Approve"), "reject": L(t"Reject"), "archive": L(t"Archive")}[action]

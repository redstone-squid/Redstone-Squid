"""Canonical tag catalogue, contribution, and moderation workspace."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Protocol, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.edit_history import history_controls, record_edit
from squid.bot.ui import tr
from squid.builds.errors import AliasAlreadyAddedError
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)
from squid.tags.application.history import AssignmentSnapshot
from squid.tags.domain import TagValueType
from squid.tags.models import TagDefinition


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

    async def edit_showcase(
        self,
        build_id: int,
        tag_id: int,
        raw_value: str | None,
        *,
        actor_account_id: int,
    ) -> tuple[TagDefinition, AssignmentSnapshot | None, AssignmentSnapshot]: ...

    async def replace_assignment(
        self,
        build_id: int,
        tag_id: int,
        expected: AssignmentSnapshot | None,
        replacement: AssignmentSnapshot | None,
        *,
        actor_account_id: int,
    ) -> bool: ...

    async def approve(self, tag_id: int) -> TagDefinition: ...

    async def reject(self, tag_id: int) -> TagDefinition: ...

    async def archive(self, tag_id: int) -> TagDefinition: ...


class RestrictionOperations(Protocol):
    """Restriction taxonomy mutation exposed by the workspace."""

    async def add_alias(self, restriction: str, alias: str) -> None: ...

    async def replace_alias(self, restriction: str, alias: str, expected: bool, replacement: bool) -> bool: ...


type TagAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type ModerationRequest = Callable[[sl.PressEvent, TagDefinition, str], Awaitable[None]]


class _ModerationActions(sl.Component[sl.ComponentsV2Target, sd.Request]):
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

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
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
    """A tag workspace that ends when replaced or timed out."""

    history: sl.runtime.History = sl.runtime.history(limit=20)
    _catalogue: sp.Browser[TagDefinition, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )
    _pending: sp.Browser[TagDefinition, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )
    _tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )
    _decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )
    _pending_action: tuple[TagDefinition, str] | None = sl.state(default=None, persist=False)

    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("tags"))

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

    @override
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
                lambda tag: (
                    _ModerationActions(tag, ("archive",), self._request_moderation)
                    if TAG_PROPOSAL_ARCHIVE in self._capabilities
                    else _tag_fields(tag)
                )
            ),
            page_size=15,
            title=tr(t"bot-tags-view-published-build-tags = Published build tags"),
            empty=tr(t"bot-tags-view-no-public-build-tags-are-available = No public build tags are available."),
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
                detail=lambda tag: (
                    _ModerationActions(tag, actions, self._request_moderation) if actions else _tag_fields(tag)
                ),
                page_size=15,
                title=tr(t"bot-tags-view-tag-proposals-awaiting-review = Tag proposals awaiting review"),
                empty=tr(t"tags-no-proposals-awaiting-review = No tag proposals are awaiting review."),
            )
        self._build_tabs()

    def _build_tabs(self) -> None:
        assert self._catalogue is not None
        tabs = [sp.Tab("catalogue", tr(t"bot-tags-view-catalogue = Catalogue"), self._catalogue)]
        tabs.append(
            sp.Tab("contribute", tr(t"bot-tags-view-propose-and-apply = Propose and apply"), self._contribution_nodes())
        )
        if self._pending is not None:
            tabs.append(sp.Tab("moderation", tr(t"bot-tags-view-moderation = Moderation"), self._pending))
        if RESTRICTION_ALIAS_CREATE in self._capabilities:
            tabs.append(
                sp.Tab("aliases", tr(t"bot-tags-view-restriction-aliases = Restriction aliases"), self._alias_nodes())
            )
        self._tabs = sp.Tabs(tabs, key="tag-tabs", title=tr(t"bot-tags-view-build-tags = Build tags"))

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._pending_action is not None and self._decision is not None:
            tag, action = self._pending_action
            tag_name = tag.display_name
            action_label = _action_label(action)
            return (
                sl.section(
                    sl.heading(tr(t"bot-tags-view-confirm-tag-moderation = Confirm tag moderation")),
                    sl.paragraph(tr(t"bot-tags-view-value-3 = {action_label} **{tag_name}**?")),
                ),
                self.boundary(self._decision, key="moderation-decision"),
            )
        if self._tabs is None:
            return (sl.status(tr(t"bot-tags-view-loading-build-tags = Loading build tags.")),)
        return (self.boundary(self._tabs, key="tabs"), *history_controls(self.history))

    def _contribution_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._actor_account_id is None:
            return (
                sl.note(
                    tr(
                        t"tags-link-account-instructions = Link and consent an account in "
                        t"`/account` before proposing or applying tags."
                    )
                ),
            )
        assignment = _ApplyForm().spec()
        if self._build_id is not None:
            assignment = replace(
                assignment,
                items=tuple(
                    field
                    for field in assignment.items
                    if not isinstance(field, sl.forms.FormField) or field.key != "build_id"
                ),
            )
        return (
            sl.form(
                tr(t"bot-tags-view-propose-tag = Propose tag"),
                self._proposal_form(),
                key="propose",
                on_submit=self._propose,
            ),
            sl.form(
                tr(t"bot-tags-view-apply-tag-to-build = Apply tag to build"),
                assignment,
                key="apply",
                on_submit=self._apply,
            ),
        )

    @staticmethod
    def _proposal_form() -> sl.forms.FormSpec:
        return _ProposalForm().spec()

    def _alias_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            sl.form(
                tr(t"bot-tags-view-add-restriction-alias = Add restriction alias"),
                _AliasForm().spec(),
                key="restriction-alias",
                on_submit=self._add_alias,
            ),
        )

    async def _propose(self, event: sl.SubmitEvent) -> None:
        account_id = self._actor_account_id
        if account_id is None:
            await event.notice(tr(t"tags-account-consent-required = A linked, consented account is required."))
            return
        form = _ProposalForm.from_submission(event)
        name = form.name
        value_type = form.value_type
        query_name = form.query_name
        tag = await self._tags.propose_showcase(
            name,
            value_type=value_type,
            query_name=query_name,
            created_by_account_id=account_id,
        )
        tag_id = tag.id
        await self._refresh()
        await event.notice(
            tr(
                t"bot-tags-view-tag-is-awaiting-staff-approval = Tag #{(tag_id_text := str(tag_id))} is awaiting staff approval."  # noqa: F841
            )
        )

    async def _apply(self, event: sl.SubmitEvent) -> None:
        account_id = self._actor_account_id
        if account_id is None:
            await event.notice(tr(t"tags-account-consent-required = A linked, consented account is required."))
            return
        form = _ApplyForm.from_submission(event)
        build_id = self._build_id or form.build_id
        tag_id = form.tag_id
        assert build_id is not None and tag_id is not None
        value = form.value
        tag, before, after = await self._tags.edit_showcase(build_id, tag_id, value, actor_account_id=account_id)

        async def authorize() -> bool:
            # Ownership and tag publication are rechecked inside the conditional write.
            return self._actor_account_id == account_id

        async def replace_assignment(
            expected: AssignmentSnapshot | None, replacement: AssignmentSnapshot | None
        ) -> bool:
            return await self._tags.replace_assignment(
                build_id, tag_id, expected, replacement, actor_account_id=account_id
            )

        record_edit(
            self.history,
            tr(t"tags-history-assignment = Edit showcase tag"),
            before,
            after,
            target=f"build:{build_id}:tag:{tag_id}",
            authorize=authorize,
            replace=replace_assignment,
            refresh=self._refresh,
        )
        tag_name = tag.display_name
        await event.notice(
            tr(
                t"bot-tags-view-attached-to-build = Attached **{tag_name}** to build #{(build_id_text := str(build_id))}."  # noqa: F841
            )
        )

    async def _add_alias(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, RESTRICTION_ALIAS_CREATE):
            return
        form = _AliasForm.from_submission(event)
        restriction = form.restriction
        alias = form.alias
        try:
            await self._restrictions.add_alias(restriction, alias)
        except AliasAlreadyAddedError:
            await event.notice(tr(t"tags-alias-already-exists = That alias is already on this restriction."))
            return

        async def replace_alias(expected: bool, replacement: bool) -> bool:
            return await self._restrictions.replace_alias(restriction, alias, expected, replacement)

        record_edit(
            self.history,
            tr(t"tags-history-alias = Add restriction alias"),
            before=False,
            after=True,
            target=f"restriction:{restriction}:alias:{alias}",
            authorize=lambda: self._authorize(RESTRICTION_ALIAS_CREATE),
            replace=replace_alias,
            refresh=self._refresh,
        )
        await event.notice(tr(t"bot-tags-view-restriction-alias-added = Restriction alias added."))

    async def _request_moderation(
        self,
        _event: sl.PressEvent,
        tag: TagDefinition,
        action: str,
    ) -> None:
        self._pending_action = (tag, action)
        self._decision = sp.Decision[sl.ComponentsV2Target, sd.Request](
            tr(t"tags-public-catalogue-warning = This changes the public tag catalogue and cannot be undone here."),
            (
                sp.DecisionOption("confirm", tr(t"bot-tags-view-confirm = Confirm"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"bot-tags-view-cancel = Cancel")),
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
        self.history.clear()
        tag_name = changed.display_name
        action_label = _action_label(action)
        self._pending_action = None
        self._decision = None
        await self._refresh()
        await event.source.notice(tr(t"bot-tags-view-value-2 = {action_label} **{tag_name}**."))

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(t"tags-operation-permission-denied = You are no longer allowed to perform this tag operation.")
        )
        return False


def _tag_summary(tag: TagDefinition) -> str:
    return f"{tag.display_name} · {tag.semantic_kind.value} · {tag.value_type.value}"


def _tag_fields(tag: TagDefinition) -> sl.semantic.Fields:
    return sl.fields(
        sl.field(tr(t"bot-tags-view-id = ID"), str(tag.id)),
        sl.field(tr(t"bot-tags-view-kind = Kind"), tag.semantic_kind.value),
        sl.field(tr(t"bot-tags-view-value = Value"), tag.value_type.value),
        sl.field(tr(t"bot-tags-view-query-field = Query field"), tag.query_name or "—"),
    )


def _action_label(action: str) -> sl.TextLike:
    return {
        "approve": tr(t"bot-tags-view-approve = Approve"),
        "reject": tr(t"bot-tags-view-reject = Reject"),
        "archive": tr(t"bot-tags-view-archive = Archive"),
    }[action]


class _ProposalForm(sl.forms.Form):
    title = tr(t"bot-tags-view-propose-showcase-tag = Propose showcase tag")

    name = sl.forms.TextField(label=tr(t"bot-tags-view-display-name = Display name"), maximum=80)
    value_type = sl.forms.ChoiceField(
        label=tr(t"bot-tags-view-value-type = Value type"),
        default=TagValueType.NONE,
        options=tuple(sl.forms.ChoiceOption(kind.value, kind.value.title(), kind) for kind in TagValueType),
    )
    query_name = sl.forms.TextField(label=tr(t"bot-tags-view-query-field = Query field"), required=False, maximum=64)


class _AliasForm(sl.forms.Form):
    title = tr(t"bot-tags-view-add-restriction-alias = Add restriction alias")

    restriction = sl.forms.TextField(
        label=tr(t"bot-tags-view-canonical-restriction = Canonical restriction"), maximum=100
    )
    alias = sl.forms.TextField(label=tr(t"bot-tags-view-new-alias = New alias"), maximum=100)


class _ApplyForm(sl.forms.Form):
    title = tr(t"bot-tags-view-apply-showcase-tag = Apply showcase tag")

    build_id = sl.forms.IntField(label=tr(t"bot-tags-view-build-id = Build ID"), minimum=1)
    tag_id = sl.forms.IntField(label=tr(t"bot-tags-view-tag-id = Tag ID"), minimum=1)
    value = sl.forms.TextField(label=tr(t"bot-tags-view-value = Value"), required=False, maximum=300)

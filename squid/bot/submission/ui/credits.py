"""Edit credit rows without rewriting provider identities or attribution evidence."""

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any, cast, override
from uuid import UUID

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest
from squid.bot.ui import tr
from squid.core.actors import ActingContext
from squid.core.builds.domain.credits import Credit, CreditKind, CreditRole, read_credits
from squid.core.suggestions.domain import SuggestionRequest
from squid_ui.ext.discord.form_fields import EntityField
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    from squid.bot.submission.ui.sections import AnswerWorkspace


def role_field(default: CreditRole = CreditRole.CREATOR) -> sl.forms.ChoiceField[CreditRole]:
    return sl.forms.ChoiceField(
        key="role",
        label=tr(t"credits-role = Contribution"),
        default=default,
        options=tuple(sl.forms.ChoiceOption(role.value, role.value.title(), role) for role in CreditRole),
    )


def edit_credit(credit: Credit, name: str, role: CreditRole) -> Credit:
    return replace(credit, name=name, role=role)


class CreditEditor(sl.Component[ComponentsV2Target, sd.Request]):
    matches: tuple[tuple[Credit, str], ...] = sl.state(())
    match_revision: int = sl.state(0)

    def __init__(self, workspace: AnswerWorkspace, field_id: str) -> None:
        self.workspace = workspace
        self.field_id = field_id

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        credits = tuple(read_credits(self.workspace.answers.get(self.field_id, [])))
        revision = self.workspace.revision
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [sl.heading(tr(t"credits-title = Credits"))]
        rows: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = []
        for index, credit in enumerate(credits):
            profile_id = credit.proposed_account_id
            rows.append(
                sl.stack(
                    sl.paragraph(f"{credit.name} · {credit.kind.value.replace('_', ' ')} · {credit.role.value}"),
                    *(
                        [sl.note(tr(t"credits-proposed-account = Proposed profile: {profile_id!s}"))]
                        if credit.proposed_account_id
                        else []
                    ),
                    sl.action_controls(
                        sl.form(
                            tr(t"credits-edit = Edit credit"),
                            sl.forms.FormSpec[sd.Request](
                                credit.name[:45],
                                (
                                    sl.forms.TextField(
                                        key="name",
                                        label=tr(t"credits-name = Display name"),
                                        default=credit.name,
                                        maximum=120,
                                    ),
                                    role_field(credit.role),
                                    *(
                                        (
                                            sl.forms.ChoiceField(
                                                key="profile",
                                                label=tr(t"credits-profile = Profile"),
                                                default="keep",
                                                options=(
                                                    sl.forms.ChoiceOption("keep", "Keep profile", "keep"),
                                                    sl.forms.ChoiceOption("clear", "Clear profile", "clear"),
                                                ),
                                            ),
                                        )
                                        if credit.proposed_account_id
                                        else ()
                                    ),
                                ),
                            ),
                            key=f"edit-{index}",
                            on_submit=partial(self._edit, credits, index, revision),
                        ),
                        sl.action_control(
                            tr(t"credits-remove = Remove credit"),
                            partial(self._remove, credits, index, revision),
                            key=f"remove-{index}",
                        ),
                        key=f"credit-{index}",
                    ),
                ).unbreakable()
            )
        if rows:
            nodes.append(sl.region(*rows, key="credits", title="", page_size=5))
        nodes.append(
            sl.action_controls(
                sl.form(
                    tr(t"credits-add-name = Add name or Minecraft player"),
                    sl.forms.FormSpec[sd.Request](
                        tr(t"credits-add = Add credit"),
                        (
                            sl.forms.ChoiceField(
                                key="kind",
                                label=tr(t"credits-kind = Name type"),
                                default=CreditKind.NAME,
                                options=tuple(
                                    sl.forms.ChoiceOption(kind.value, label, kind)
                                    for kind, label in (
                                        (CreditKind.NAME, "Name"),
                                        (CreditKind.MINECRAFT_JAVA, "Minecraft Java"),
                                        (CreditKind.MINECRAFT_BEDROCK, "Minecraft Bedrock"),
                                    )
                                ),
                            ),
                            sl.forms.TextField(
                                key="name", label=tr(t"credits-player-name = Name or username"), maximum=120
                            ),
                            *(
                                (
                                    sl.forms.TextField(
                                        key="profile_query",
                                        label=tr(t"credits-nominate = Credit a public account (optional)"),
                                        placeholder=tr(t"credits-profile-query = Search by name or public account ID"),
                                        required=False,
                                        maximum=120,
                                    ),
                                )
                                if self.workspace.can_nominate_accounts
                                else ()
                            ),
                            role_field(),
                        ),
                    ),
                    key="add-name",
                    on_submit=partial(self._add, credits, revision),
                ),
                sl.form(
                    tr(t"credits-add-discord = Choose Discord user"),
                    sl.forms.FormSpec[sd.Request](
                        tr(t"credits-add = Add credit"),
                        (
                            EntityField(
                                key="user",
                                label=tr(t"credits-discord-user = Discord user"),
                                entity_type=sl.entity.EntityType.USER,
                            ),
                            role_field(),
                        ),
                    ),
                    key="add-discord",
                    on_submit=partial(self._add_discord, credits, revision),
                ),
                sl.form(
                    tr(t"credits-add-reference = Add identity reference"),
                    _IdentityReferenceForm().spec(),
                    key="identity-reference",
                    on_submit=partial(self._add_reference, credits, revision),
                ),
                key="add-credit",
            )
        )
        if self.matches and revision == self.match_revision:
            nodes.append(
                sd.v2.select(
                    *(
                        sd.v2.option(
                            label[:100],
                            str(index),
                            description=(
                                f"Public profile · {match.proposed_account_id}"
                                if match.proposed_account_id
                                else "Credited name"
                            ),
                        )
                        for index, (match, label) in enumerate(self.matches)
                    ),
                    on_select=self._pick,
                    key="credit-match",
                    placeholder=tr(t"credits-select = Select a name or public profile"),
                )
            )
        return tuple(nodes)

    async def _pick(self, event: sd.SelectionEvent) -> None:
        credit, _ = self.matches[int(event.values[0])]
        credits = tuple(read_credits(self.workspace.answers.get(self.field_id, [])))
        await self._save(event.transaction, (*credits, credit), self.match_revision)
        self.matches = ()

    async def _save(self, transaction: sl.runtime.Transaction, credits: tuple[Credit, ...], revision: int) -> None:
        await self.workspace.save_values(
            transaction, {self.field_id: [credit.to_json() for credit in credits]}, base_revision=revision
        )

    async def _edit(self, credits: tuple[Credit, ...], index: int, revision: int, event: sd.SubmitEvent) -> None:
        changed = edit_credit(credits[index], str(event.values["name"]), cast(CreditRole, event.values["role"]))
        if event.values.get("profile") == "clear":
            changed = replace(changed, proposed_account_id=None)
        await self._save(event.transaction, (*credits[:index], changed, *credits[index + 1 :]), revision)

    async def _remove(self, credits: tuple[Credit, ...], index: int, revision: int, event: sd.PressEvent) -> None:
        await self._save(event.transaction, (*credits[:index], *credits[index + 1 :]), revision)

    async def _add(self, credits: tuple[Credit, ...], revision: int, event: sd.SubmitEvent) -> None:
        kind = cast(CreditKind, event.values["kind"])
        name = str(event.values["name"])
        credit = Credit(
            kind=kind,
            name=name,
            username=name if kind is not CreditKind.NAME else None,
            role=cast(CreditRole, event.values["role"]),
        )
        profile_query = str(event.values.get("profile_query") or "").strip()
        if self.workspace.can_nominate_accounts and (kind is CreditKind.NAME or profile_query):
            service = cast(AppRequest, event.context).client.services.suggestions
            choices = [] if profile_query else [(credit, credit.name)]
            sources = ("creator_profiles",) if profile_query else ("creators", "creator_profiles")
            for source in sources:
                result = await service.suggest(
                    SuggestionRequest(source, query=profile_query or name, limit=10), context=ActingContext.current()
                )
                for match in result.items:
                    choice = (
                        replace(credit, proposed_account_id=UUID(match.value))
                        if source == "creator_profiles"
                        else replace(credit, name=match.label)
                    )
                    if (choice, match.label) not in choices:
                        choices.append((choice, match.label))
            if not choices:
                self.matches = ()
                await event.notice(tr(t"credits-no-profile = No public account matched that search."))
                return
            if profile_query or len(choices) > 1:
                self.matches = tuple(choices)
                self.match_revision = revision
                return
        await self._save(event.transaction, (*credits, credit), revision)

    async def _add_discord(self, credits: tuple[Credit, ...], revision: int, event: sd.SubmitEvent) -> None:
        user = cast(discord.User | discord.Member, event.values["user"])
        credit = Credit(
            kind=CreditKind.DISCORD,
            name=user.display_name,
            username=user.name,
            subject=str(user.id),
            role=cast(CreditRole, event.values["role"]),
        )
        await self._save(event.transaction, (*credits, credit), revision)

    async def _add_reference(self, credits: tuple[Credit, ...], revision: int, event: sd.SubmitEvent) -> None:
        form = _IdentityReferenceForm.from_submission(event)
        username = form.kind == "discord_username"
        credit = Credit(
            kind=CreditKind.DISCORD if username else CreditKind(form.kind),
            name=form.name,
            username=form.reference.removeprefix("@") if username else None,
            subject=None if username else form.reference.strip(),
            role=form.role,
        )
        await self._save(event.transaction, (*credits, credit), revision)


class _IdentityReferenceForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"credits-add-reference = Add identity reference")

    kind = sl.forms.ChoiceField(
        label=tr(t"credits-reference-type = Identity type"),
        options=(
            sl.forms.ChoiceOption("discord", tr(t"credits-reference-discord = Discord user ID"), "discord"),
            sl.forms.ChoiceOption(
                "discord_username", tr(t"credits-reference-discord-name = Discord username"), "discord_username"
            ),
            sl.forms.ChoiceOption(
                "minecraft_java", tr(t"credits-reference-java = Minecraft Java UUID"), "minecraft_java"
            ),
            sl.forms.ChoiceOption(
                "minecraft_bedrock", tr(t"credits-reference-bedrock = Minecraft Bedrock XUID"), "minecraft_bedrock"
            ),
        ),
    )
    reference = sl.forms.TextField(
        label=tr(t"credits-reference = Identity reference"),
        description=tr(t"credits-reference-help = Paste the ID or username for the identity type you chose."),
        maximum=100,
    )
    name = sl.forms.TextField(label=tr(t"credits-name = Display name"), maximum=120)
    role = role_field()

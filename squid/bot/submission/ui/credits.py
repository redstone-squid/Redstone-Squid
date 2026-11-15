"""Edit credit rows without rewriting provider identities or attribution evidence."""

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, cast, override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.ui import tr
from squid.builds.domain.credits import Credit, CreditKind, CreditRole, read_credits
from squid_ui.ext.discord.form_fields import EntityField

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


class CreditEditor(sl.Component[sl.ComponentsV2Target, sd.Request]):
    page: int = sl.state(0)

    def __init__(self, workspace: AnswerWorkspace, field_id: str) -> None:
        self.workspace = workspace
        self.field_id = field_id

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        credits = tuple(read_credits(self.workspace.answers.get(self.field_id, [])))
        revision = self.workspace.revision
        page = min(self.page, max(0, (len(credits) - 1) // 5))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [sl.heading(tr(t"credits-title = Credits"))]
        for index in range(page * 5, min(len(credits), (page + 1) * 5)):
            credit = credits[index]
            nodes.append(
                sl.stack(
                    sl.paragraph(f"{credit.name} · {credit.kind.value.replace('_', ' ')} · {credit.role.value}"),
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
                )
            )
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
        if len(credits) > 5:
            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"credits-previous = Previous credits"),
                        partial(self._page, page - 1),
                        key="previous",
                        available=page > 0,
                    ),
                    sl.action_control(
                        tr(t"credits-next = Next credits"),
                        partial(self._page, page + 1),
                        key="next",
                        available=(page + 1) * 5 < len(credits),
                    ),
                    key="credit-pages",
                )
            )
        return tuple(nodes)

    async def _save(self, transaction: sl.runtime.Transaction, credits: tuple[Credit, ...], revision: int) -> None:
        await self.workspace.save_values(
            transaction, {self.field_id: [credit.to_json() for credit in credits]}, base_revision=revision
        )

    async def _edit(self, credits: tuple[Credit, ...], index: int, revision: int, event: sd.SubmitEvent) -> None:
        changed = edit_credit(credits[index], str(event.values["name"]), cast(CreditRole, event.values["role"]))
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

    async def _page(self, page: int, event: sd.PressEvent) -> None:
        self.page = max(0, page)


class _IdentityReferenceForm(sl.forms.Form):
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

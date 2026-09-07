"""Private review of persisted recalculation candidates."""

from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.utils.permissions import subject_for_interaction
from squid.core.errors import SquidError
from squid.runtime import BotServices
from squid.submissions.application.revision_values import RevisionProposal


class RevisionProposalScreen(sd.Screen):
    """Review a persisted candidate and explicitly authorize its expected build revision."""

    audience = "personal"
    timeout = 600
    proposal: RevisionProposal = sl.state(opaque=True)
    notice: str = sl.state("")

    def __init__(self, services: BotServices, proposal: RevisionProposal, targets: tuple[int, ...]) -> None:
        self.services = services
        self.proposal = proposal
        self.targets = targets

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        proposal = self.proposal
        if proposal.applied_revision is not None:
            return (sl.status(f"Applied to build #{proposal.build_id}, revision {proposal.applied_revision}."),)
        changes = [
            f"{name.replace('_', ' ')}: {proposal.before.get(name)!s} → {value!s}"
            for name, value in proposal.after.items()
            if proposal.before.get(name) != value
        ]
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading("Review recalculation"),
                sl.paragraph(
                    self.notice
                    or ("\n".join(changes)[:3000] if changes else "Choose a target build to review the changes.")
                ),
            )
        ]
        if proposal.build_id is None:
            if self.targets:
                nodes.append(
                    sl.choices(
                        *(sl.choice(f"Build #{target}", key=str(target)) for target in self.targets[:25]),
                        key="target",
                        selection=sl.controlled((), self._match),
                    )
                )
            else:
                nodes.append(sl.note("No existing build is linked to this message."))
        else:
            nodes.append(sl.note(f"Build #{proposal.build_id}, reviewed revision {proposal.expected_revision}."))
            nodes.append(
                sl.action_controls(
                    sl.action_control("Approve changes", self._approve, key="approve"),
                    sl.action_control("Renew review", self._renew, key="renew"),
                    key="review",
                )
            )
        return tuple(nodes)

    async def _match(self, event: sl.ChoiceEvent) -> None:
        actor = await subject_for_interaction(await sd.request(sd.responder(event).interaction))
        try:
            self.proposal = await self.services.submission_revisions.match(
                self.proposal.id, int(event.selected[0]), actor
            )
        except SquidError as error:
            self.notice = error.public_detail()

    async def _approve(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        actor = await subject_for_interaction(await sd.request(sd.responder(event).interaction))
        try:
            self.proposal = await self.services.submission_revisions.approve(self.proposal.id, actor)
        except SquidError as error:
            self.notice = error.public_detail()

    async def _renew(self, event: sl.PressEvent) -> None:
        actor = await subject_for_interaction(await sd.request(sd.responder(event).interaction))
        if self.proposal.build_id is not None:
            self.proposal = await self.services.submission_revisions.match(
                self.proposal.id, self.proposal.build_id, actor, renew=True
            )
            self.notice = "Review the refreshed diff before approving."


async def proposal_screen(services: BotServices, proposal_id: UUID, request: sd.Request) -> RevisionProposalScreen:
    """Reload retained review state and current target choices after restart."""
    actor = await subject_for_interaction(request)
    proposal = await services.submission_revisions.get(proposal_id, actor)
    targets = tuple(await services.builds.list_ids_for_source_message(proposal.source_message_id))
    return RevisionProposalScreen(services, proposal, targets)

"""Independent reviewer histories over one invocation's shared sample review."""

from dataclasses import dataclass, replace
from typing import Literal, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.layout_inspector import InspectablePanel
from squid.bot.layout_workbench import history_outcome
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, tr


@dataclass(frozen=True)
class Review:
    decision: str = "Pending"
    comment: str = ""


class Reviewer(sl.Reactive):
    """A separate cell per seat keeps one reviewer's undo away from another's work."""

    review: Review = sl.state(Review())
    name: str = sl.state("")

    def __init__(self, user_id: int, name: str) -> None:
        self.user_id = user_id
        self.name = name


class ReviewRoom(sl.Reactive):
    seats: tuple[Reviewer, ...] = sl.state((), persist=False, opaque=True)
    closed: bool = sl.state(default=False)
    activity: tuple[str, ...] = sl.state(())

    def log(self, line: str) -> None:
        self.activity = (*self.activity[-5:], line)

    def totals(self) -> str:
        approved = sum(seat.review.decision == "Approved" for seat in self.seats)
        changes = sum(seat.review.decision == "Changes requested" for seat in self.seats)
        return f"{approved} approved · {changes} requested changes · {len(self.seats)} reviewers"


class ReviewForm(sl.forms.Form):
    title = tr("Review sample build")
    decision = sl.forms.ChoiceField(
        label=tr("Decision"),
        options=(
            sl.forms.ChoiceOption("approve", tr("Approve"), "Approved"),
            sl.forms.ChoiceOption("changes", tr("Request changes"), "Changes requested"),
        ),
    )
    comment = sl.forms.TextAreaField(label=tr("Comment"), required=False, maximum=400)

    def __init__(self, panel: ReviewerPanel) -> None:
        review = panel.seat.review
        super().__init__(
            decision=review.decision if review.decision != "Pending" else "Approved", comment=review.comment
        )
        self.panel = panel

    @override
    def validate(self) -> tuple[sl.forms.FormIssue, ...]:
        if self.decision == "Changes requested" and not self.comment:
            return (sl.forms.FieldError(ReviewForm.comment, message=tr("Describe the change you want.")),)
        return ()

    @override
    async def on_submit(self, event: sl.SubmitEvent) -> None:
        # The room can close while a modal is open, after the opening guard ran.
        if self.panel.room.closed:
            await event.notice(tr("This review room has closed."))
            return
        self.panel.seat.review = Review(self.decision, self.comment or "")
        self.panel.history.record(tr("Edit review"))
        self.panel.record_activity(f"{self.panel.seat.name}: {self.decision}")


class ReviewerPanel(InspectablePanel):
    history: sl.runtime.History = sl.runtime.history(limit=10)
    outcome: str = sl.state("Submit a review, then undo it. The other reviewer's contribution stays.", persist=False)

    def __init__(self, room: ReviewRoom, seat: Reviewer, *, simulated: bool) -> None:
        self.room = room
        self.seat = seat
        self.simulated = simulated

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        review = self.seat.review
        heading = sl.heading(f"{self.seat.name} · {'simulated reviewer' if self.simulated else 'reviewer'}")
        children: tuple[sl.ChildLike[sl.ComponentsV2Target, sd.Request], ...] = (
            sl.paragraph(self.room.totals()),
            sl.fields(
                sl.field(tr("Your decision"), review.decision), sl.field(tr("Your comment"), review.comment or "—")
            ),
            sl.form(tr("Write review"), ReviewForm(self), key="review"),
            sl.action_controls(
                sl.action_control(tr("Approve"), self._approve, key="approve", available=not self.room.closed),
                sl.action_control(
                    tr("Undo mine"), self._undo, key="undo", available=self.history.can_undo and not self.room.closed
                ),
                sl.action_control(
                    tr("Redo mine"), self._redo, key="redo", available=self.history.can_redo and not self.room.closed
                ),
                key="review-actions",
            ),
            sl.note(self.outcome),
            self.inspector(),
        )
        return sl.section(heading, *children, accent=DISCORD_GREEN)

    @override
    def inspection_state(self) -> str:
        return (
            f"Seat: {self.seat.name} · decision={self.seat.review.decision}\n"
            f"Local history: undo={self.history.can_undo}, redo={self.history.can_redo}\n"
            f"Last outcome: {self.outcome}\nShared totals: {self.room.totals()}\n"
            "Each seat owns a separate shared cell. Undo records that seat, not the room totals."
        )

    def record_activity(self, line: str) -> None:
        # Activity is an audit of actions, not part of a reviewer's undoable contribution.
        def committed(commit: sl.runtime.ActionCommit, continuation: sl.runtime.ActionContinuation) -> None:
            with continuation.start_action("Log review activity"):
                self.room.log(line)

        sl.runtime.on_action_commit(committed)

    async def _approve(self, event: sl.PressEvent[sd.Request]) -> None:
        if self.room.closed:
            return
        self.seat.review = replace(self.seat.review, decision="Approved")
        self.history.record(tr("Approve build"))
        self.record_activity(f"{self.seat.name}: Approved")

    async def _undo(self, event: sl.PressEvent[sd.Request]) -> None:
        if not self.room.closed:
            self.outcome = history_outcome("Undo", await self.history.undo())
            self.record_activity(f"{self.seat.name}: {self.outcome}")

    async def _redo(self, event: sl.PressEvent[sd.Request]) -> None:
        if not self.room.closed:
            self.outcome = history_outcome("Redo", await self.history.redo())
            self.record_activity(f"{self.seat.name}: {self.outcome}")


class ReviewSummary(InspectablePanel):
    def __init__(self, room: ReviewRoom, host_id: int, mode: Literal["simulated", "live"]) -> None:
        self.room = room
        self.host_id = host_id
        self.mode = mode
        self.panels: dict[int, sd.MessageRoot] = {}

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        heading = sl.heading(tr("Review room · Flush piston door"))
        children: tuple[sl.ChildLike[sl.ComponentsV2Target, sd.Request], ...] = (
            sl.paragraph(
                tr("Disposable reviews of a 4x4x3 door opening in 16 ticks. This does not create a production vote.")
            ),
            sl.note(
                tr("Approve as Alice and Bob, then undo Alice. Bob's approval stays.")
                if self.mode == "simulated"
                else tr("Join to open your own reviewer panel. Invite someone else to review too.")
            ),
            sl.paragraph(self.room.totals()),
            sd.v2.lines(
                *(f"{seat.name}: {seat.review.decision}" for seat in self.room.seats),
                overflow=sl.primitives.Paginate(key="reviewers", per=5),
            )
            if self.room.seats
            else sl.note(tr("Waiting for reviewers.")),
            sl.heading(tr("Recent activity")),
            sl.paragraph("\n".join(self.room.activity) or "No reviews yet."),
            sl.action_controls(
                sl.action_control(
                    tr("Join review"), self._join, key="join", available=self.mode == "live" and not self.room.closed
                ),
                sl.action_control(
                    tr("Close room · host only"), self._close, key="close", available=not self.room.closed
                ),
                key="room-actions",
            ),
            self.inspector(),
        )
        return sl.section(heading, *children, accent=DISCORD_BLUE)

    @override
    def inspection_state(self) -> str:
        return (
            f"Mode: {self.mode} · closed={self.room.closed}\n{self.room.totals()}\n"
            "Totals are derived from reviewer cells. These messages share local state, not network replicas."
        )

    async def _join(self, event: sl.PressEvent[sd.Request]) -> None:
        if self.mode != "live" or self.room.closed:
            return
        user_id = int(event.actor.id)
        existing = self.panels.get(user_id)
        if existing is not None and not existing.finished:
            await event.notice(tr("You already have a reviewer panel in this room."))
            return
        seat = next((seat for seat in self.room.seats if seat.user_id == user_id), None)
        if seat is None:
            seat = Reviewer(user_id, f"<@{user_id}>")
        result = await event.context.respond(
            ReviewerPanel(self.room, seat, simulated=False),
            access=sd.Owner(user_id),
            audience="public",
            live_updates=True,
            parent=event.context.root,
            session=sd.SessionSpec("layout-reviewer"),
        )
        if isinstance(result, sd.Presented):
            self.panels[user_id] = result.root
            if seat not in self.room.seats:
                self.room.seats = (*self.room.seats, seat)

    async def _close(self, event: sl.PressEvent[sd.Request]) -> None:
        if int(event.actor.id) != self.host_id:
            await event.notice(tr("Only the host can close this room."))
            return
        self.room.closed = True
        event.finish_on_success()

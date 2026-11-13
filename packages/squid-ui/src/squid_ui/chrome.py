"""Host-overridable framework labels and status messages.

Supply localized TextLike values or callbacks instead of relying on the host's
translation extractor to scan this package. Form parse errors and renderer-specific
wording are defined separately.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from squid_i18n import md_tr, tr
from squid_ui.context import ContextKey
from squid_ui.text import Localization, TextLike, resolve_text


def _default_and_n_more(count: int) -> TextLike:
    return md_tr(t"ui-chrome-and-n-more = …and {count} more")


def _default_step_progress(step: int, steps: int) -> TextLike:
    return md_tr(t"ui-chrome-step-progress = Step {step} of {steps}")


def _default_page_footer(page: int, pages: int) -> TextLike:
    return md_tr(t"ui-chrome-page-footer = Page {page} of {pages}")


def _default_range_footer(first: int, last: int) -> TextLike:
    return md_tr(t"ui-chrome-range-footer = {first}\N{EN DASH}{last}")


def _default_approximate_total_footer(first: int, last: int, total: int) -> TextLike:
    return md_tr(t"ui-chrome-approximate-total-footer = {first}\N{EN DASH}{last} of ~{total}")


def _default_total_range_footer(first: int, last: int, total: int) -> TextLike:
    return md_tr(t"ui-chrome-total-range-footer = {first}\N{EN DASH}{last} of {total}")


def _default_page_option(page: int) -> TextLike:
    return md_tr(t"ui-chrome-page-option = Page {page}")


def _default_decided(label: TextLike) -> TextLike:
    return md_tr(t"ui-chrome-decided = You chose {label}.")


def _default_try_again_in(seconds: float) -> TextLike:
    whole = max(1, ceil(seconds))
    return md_tr(t"""
        ui-chrome-try-again-in = {{ {whole} ->
            [one] Try again in {whole} second.
           *[other] Try again in {whole} seconds.
        }}
        """)


def _default_slot_count(count: int, capacity: int | None) -> TextLike:
    return (
        md_tr(t"ui-chrome-slot-count = {count}")
        if capacity is None
        else md_tr(t"ui-chrome-slot-capacity = {count}/{capacity}")
    )


def _default_approved_count(count: int, total: int) -> TextLike:
    return md_tr(t"ui-chrome-approved-count = Approved: {count}/{total}")


@dataclass(frozen=True, slots=True)
class Chrome:
    """Framework wording shared by planners, navigation controls, and mounted interactions."""

    and_n_more: Callable[[int], TextLike] = _default_and_n_more
    """Spill line appended when a list shows fewer entries than it holds."""
    not_yours: TextLike = tr(t"ui-chrome-not-yours = These controls belong to someone else.")
    """Ephemeral rejection shown when an author-locked control is used by another user."""
    session_ended: TextLike = tr(t"ui-chrome-session-ended = This session has ended.")
    """Ephemeral rejection shown when a control on a finished mount is clicked anyway."""
    not_now: TextLike = tr(t"ui-chrome-not-now = You can't do that right now.")
    """Ephemeral rejection shown when an action's guard denies a press with no wording."""
    try_again_in: Callable[[float], TextLike] = _default_try_again_in
    """Denial wording when the guard said how long to wait; called with seconds."""
    working: TextLike = tr(t"ui-chrome-working = Working…")
    """What a control with `BusySpec` says while its handler runs."""
    updates_paused: TextLike = tr(t"ui-chrome-updates-paused = Live updates paused — press any control to resume.")
    """Status shown before an interaction edit token expires and unattended refreshes pause."""
    session_expiring: TextLike = tr(t"ui-chrome-session-expiring = This session is about to expire.")
    """Status shown while an ephemeral session waits for an explicit renewal click."""
    continue_session: TextLike = tr(t"ui-chrome-continue-session = Continue Session")
    """Framework-owned action that renews an expiring ephemeral session."""
    changed_elsewhere: TextLike = tr(
        t"ui-chrome-changed-elsewhere = Someone else changed this while you were working. Try again."
    )
    """Wording for a shared-state conflict: what the action read moved before it committed."""
    sent_privately: TextLike = tr(t"ui-chrome-sent-privately = Sent by direct message.")
    """Channel confirmation after a reply was delivered by direct message."""
    dm_unavailable: TextLike = tr(
        t"ui-chrome-dm-unavailable = This reply is too private for a channel, and your direct messages "
        t"are closed. Run the command in a direct message, or allow direct messages and retry."
    )
    """Channel notice when a private reply cannot be delivered by direct message."""
    previous: TextLike = tr(t"ui-chrome-previous = Previous")
    next: TextLike = tr(t"ui-chrome-next = Next")
    older: TextLike = tr(t"ui-chrome-older = Older")
    newer: TextLike = tr(t"ui-chrome-newer = Newer")
    back: TextLike = tr(t"ui-chrome-back = Back")
    home: TextLike = tr(t"ui-chrome-home = Home")
    close: TextLike = tr(t"ui-chrome-close = Close")
    undo: TextLike = tr(t"ui-chrome-undo = Undo")
    redo: TextLike = tr(t"ui-chrome-redo = Redo")
    on: TextLike = tr(t"ui-chrome-on = On")
    off: TextLike = tr(t"ui-chrome-off = Off")
    download: TextLike = tr(t"ui-chrome-download = Download")
    confirm: TextLike = tr(t"ui-chrome-confirm = Confirm")
    cancel: TextLike = tr(t"ui-chrome-cancel = Cancel")
    apply: TextLike = tr(t"ui-chrome-apply = Apply")
    save: TextLike = tr(t"ui-chrome-save = Save")
    unsaved: TextLike = tr(t"ui-chrome-unsaved = Unsaved changes")
    search: TextLike = tr(t"ui-chrome-search = Search")
    no_results: TextLike = tr(t"ui-chrome-no-results = No results")
    decided: Callable[[TextLike], TextLike] = _default_decided
    add: TextLike = tr(t"ui-chrome-add = Add")
    edit: TextLike = tr(t"ui-chrome-edit = Edit")
    remove: TextLike = tr(t"ui-chrome-remove = Remove")
    move_up: TextLike = tr(t"ui-chrome-move-up = Move up")
    move_down: TextLike = tr(t"ui-chrome-move-down = Move down")
    review: TextLike = tr(t"ui-chrome-review = Review")
    finish: TextLike = tr(t"ui-chrome-finish = Finish")
    continue_step: TextLike = tr(t"ui-chrome-continue = Continue")
    """Opens the current wizard step's form when more steps follow it."""
    step_progress: Callable[[int, int], TextLike] = _default_step_progress
    """A wizard's position; called with (step, steps), 1-based."""
    unanswered: TextLike = tr(t"ui-chrome-unanswered = Not answered yet")
    page_footer: Callable[[int, int], TextLike] = _default_page_footer
    """Small-text footer under paginated content; called with (page, pages), 1-based."""
    range_footer: Callable[[int, int], TextLike] = _default_range_footer
    """Visible 1-based item range for a source with known offsets."""
    approximate_total_footer: Callable[[int, int, int], TextLike] = _default_approximate_total_footer
    """Visible range and approximate source total."""
    total_range_footer: Callable[[int, int, int], TextLike] = _default_total_range_footer
    """Visible range and exact source total."""
    jump_to_page: TextLike = tr(t"ui-chrome-jump-to-page = Jump to a page")
    """Placeholder on the jump select a seekable paginator can offer."""
    page_option: Callable[[int], TextLike] = _default_page_option
    """One entry of that select; called with a 1-based page number."""
    waitlist: TextLike = tr(t"ui-chrome-waitlist = Waitlist")
    full: TextLike = tr(t"ui-chrome-full = Full")
    slot_count: Callable[[int, int | None], TextLike] = _default_slot_count
    approve: TextLike = tr(t"ui-chrome-approve = Approve")
    withdraw: TextLike = tr(t"ui-chrome-withdraw = Withdraw")
    approved_count: Callable[[int, int], TextLike] = _default_approved_count


DEFAULT_CHROME = Chrome()
CHROME_CONTEXT = ContextKey[Chrome]("chrome")
LOCALIZATION_CONTEXT = ContextKey[Localization]("localization")


def localize_chrome(chrome: Chrome, localization: Localization) -> Chrome:
    """Resolve fixed labels now and wrap dynamic labels with the same localization.

    Callbacks still run when their counts, ranges, or selected labels are known.
    """
    return Chrome(
        and_n_more=lambda count: resolve_text(chrome.and_n_more(count), localization).content,
        not_yours=resolve_text(chrome.not_yours, localization).content,
        session_ended=resolve_text(chrome.session_ended, localization).content,
        not_now=resolve_text(chrome.not_now, localization).content,
        try_again_in=lambda seconds: resolve_text(chrome.try_again_in(seconds), localization).content,
        working=resolve_text(chrome.working, localization).content,
        updates_paused=resolve_text(chrome.updates_paused, localization).content,
        session_expiring=resolve_text(chrome.session_expiring, localization).content,
        continue_session=resolve_text(chrome.continue_session, localization).content,
        changed_elsewhere=resolve_text(chrome.changed_elsewhere, localization).content,
        sent_privately=resolve_text(chrome.sent_privately, localization).content,
        dm_unavailable=resolve_text(chrome.dm_unavailable, localization).content,
        previous=resolve_text(chrome.previous, localization).content,
        next=resolve_text(chrome.next, localization).content,
        older=resolve_text(chrome.older, localization).content,
        newer=resolve_text(chrome.newer, localization).content,
        back=resolve_text(chrome.back, localization).content,
        home=resolve_text(chrome.home, localization).content,
        close=resolve_text(chrome.close, localization).content,
        undo=resolve_text(chrome.undo, localization).content,
        redo=resolve_text(chrome.redo, localization).content,
        on=resolve_text(chrome.on, localization).content,
        off=resolve_text(chrome.off, localization).content,
        download=resolve_text(chrome.download, localization).content,
        confirm=resolve_text(chrome.confirm, localization).content,
        cancel=resolve_text(chrome.cancel, localization).content,
        apply=resolve_text(chrome.apply, localization).content,
        save=resolve_text(chrome.save, localization).content,
        unsaved=resolve_text(chrome.unsaved, localization).content,
        search=resolve_text(chrome.search, localization).content,
        no_results=resolve_text(chrome.no_results, localization).content,
        decided=lambda label: resolve_text(chrome.decided(label), localization).content,
        add=resolve_text(chrome.add, localization).content,
        edit=resolve_text(chrome.edit, localization).content,
        remove=resolve_text(chrome.remove, localization).content,
        move_up=resolve_text(chrome.move_up, localization).content,
        move_down=resolve_text(chrome.move_down, localization).content,
        review=resolve_text(chrome.review, localization).content,
        finish=resolve_text(chrome.finish, localization).content,
        continue_step=resolve_text(chrome.continue_step, localization).content,
        step_progress=lambda step, steps: resolve_text(chrome.step_progress(step, steps), localization).content,
        unanswered=resolve_text(chrome.unanswered, localization).content,
        page_footer=lambda page, pages: resolve_text(chrome.page_footer(page, pages), localization).content,
        range_footer=lambda first, last: resolve_text(chrome.range_footer(first, last), localization).content,
        approximate_total_footer=lambda first, last, total: (
            resolve_text(chrome.approximate_total_footer(first, last, total), localization).content
        ),
        total_range_footer=lambda first, last, total: (
            resolve_text(chrome.total_range_footer(first, last, total), localization).content
        ),
        jump_to_page=resolve_text(chrome.jump_to_page, localization).content,
        page_option=lambda page: resolve_text(chrome.page_option(page), localization).content,
        waitlist=resolve_text(chrome.waitlist, localization).content,
        full=resolve_text(chrome.full, localization).content,
        slot_count=lambda count, capacity: resolve_text(chrome.slot_count(count, capacity), localization).content,
        approve=resolve_text(chrome.approve, localization).content,
        withdraw=resolve_text(chrome.withdraw, localization).content,
        approved_count=lambda count, total: resolve_text(chrome.approved_count(count, total), localization).content,
    )


__all__ = [
    "CHROME_CONTEXT",
    "DEFAULT_CHROME",
    "LOCALIZATION_CONTEXT",
    "Chrome",
    "localize_chrome",
]

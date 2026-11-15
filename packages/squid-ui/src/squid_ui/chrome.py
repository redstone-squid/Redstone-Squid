"""Host-overridable framework labels and status messages.

Defaults are English. Applications supply localized TextLike values or callbacks
and own the corresponding translation catalogs. Form parsers and widgets use the
same wording contract as mounted controls.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from squid_ui.content.text import Localization, TextLike, md, resolve_text
from squid_ui.context import ContextKey


def _default_and_n_more(count: int) -> TextLike:
    return md(t"…and {count} more")


def _default_step_progress(step: int, steps: int) -> TextLike:
    return md(t"Step {step} of {steps}")


def _default_page_footer(page: int, pages: int) -> TextLike:
    return md(t"Page {page} of {pages}")


def _default_range_footer(first: int, last: int) -> TextLike:
    return md(t"{first}\N{EN DASH}{last}")


def _default_approximate_total_footer(first: int, last: int, total: int) -> TextLike:
    return md(t"{first}\N{EN DASH}{last} of ~{total}")


def _default_total_range_footer(first: int, last: int, total: int) -> TextLike:
    return md(t"{first}\N{EN DASH}{last} of {total}")


def _default_page_option(page: int) -> TextLike:
    return md(t"Page {page}")


def _default_decided(label: TextLike) -> TextLike:
    return md(t"You chose {label}.")


def _default_try_again_in(seconds: float) -> TextLike:
    whole = max(1, ceil(seconds))
    unit = "second" if whole == 1 else "seconds"
    return md(t"Try again in {whole} {unit}.")


def _default_slot_count(count: int, capacity: int | None) -> TextLike:
    return md(t"{count}") if capacity is None else md(t"{count}/{capacity}")


def _default_approved_count(count: int, total: int) -> TextLike:
    return md(t"Approved: {count}/{total}")


def _default_form_text_minimum(minimum: int) -> TextLike:
    return md(t"Enter at least {minimum} characters.")


def _default_form_text_maximum(maximum: int) -> TextLike:
    return md(t"Enter no more than {maximum} characters.")


def _default_form_number_minimum(minimum: int | float) -> TextLike:
    return md(t"Enter a value of at least {minimum}.")


def _default_form_number_maximum(maximum: int | float) -> TextLike:
    return md(t"Enter a value no greater than {maximum}.")


def _default_form_duration_minimum(minimum: int | float) -> TextLike:
    return md(t"Enter a duration of at least {minimum} seconds.")


def _default_form_duration_maximum(maximum: int | float) -> TextLike:
    return md(t"Enter a duration no longer than {maximum} seconds.")


def _default_form_date_minimum(value: str) -> TextLike:
    return md(t"Enter a date on or after {value}.")


def _default_form_date_maximum(value: str) -> TextLike:
    return md(t"Enter a date on or before {value}.")


def _default_form_time_minimum(value: str) -> TextLike:
    return md(t"Enter a time at or after {value}.")


def _default_form_time_maximum(value: str) -> TextLike:
    return md(t"Enter a time at or before {value}.")


def _default_form_datetime_minimum(value: str) -> TextLike:
    return md(t"Enter a date and time on or after {value}.")


def _default_form_datetime_maximum(value: str) -> TextLike:
    return md(t"Enter a date and time on or before {value}.")


def _default_form_scale_range(minimum: int | float, maximum: int | float) -> TextLike:
    return md(t"Choose a value from {minimum} to {maximum}.")


def _default_form_selection_minimum(minimum: int) -> TextLike:
    return md(t"Choose at least {minimum} options.")


def _default_form_selection_maximum(maximum: int) -> TextLike:
    return md(t"Choose no more than {maximum} options.")


def _default_form_scale_integer(minimum: int | float, maximum: int | float) -> TextLike:
    return md(t"Choose a whole number from {minimum} to {maximum}.")


def _default_page_jump_range(pages: int) -> TextLike:
    return md(t"Enter a page from 1 to {pages}.")


def _default_tally_option(label: TextLike, count: int) -> TextLike:
    return md(t"{label} — {count}")


def _default_agreement_participant(mark: str, display: TextLike) -> TextLike:
    return md(t"{mark} {display}")


def _default_collection_too_few(minimum: int) -> TextLike:
    unit = "entry" if minimum == 1 else "entries"
    return md(t"Add at least {minimum} {unit}.")


def _default_collection_too_many(maximum: int) -> TextLike:
    unit = "entry" if maximum == 1 else "entries"
    return md(t"Keep no more than {maximum} {unit}.")


def _default_multi_choice_too_few(minimum: int) -> TextLike:
    unit = "option" if minimum == 1 else "options"
    return md(t"Select at least {minimum} {unit}.")


def _default_multi_choice_too_many(maximum: int) -> TextLike:
    unit = "option" if maximum == 1 else "options"
    return md(t"Select no more than {maximum} {unit}.")


def _default_multi_choice_exclusive(group_label: TextLike) -> TextLike:
    return md(t"{group_label} cannot be combined with an exclusive group.")


@dataclass(frozen=True, slots=True)
class Chrome:
    """Framework wording shared by planners, navigation controls, and mounted interactions."""

    and_n_more: Callable[[int], TextLike] = _default_and_n_more
    """Spill line appended when a list shows fewer entries than it holds."""
    not_yours: TextLike = "These controls belong to someone else."
    """Ephemeral rejection shown when an author-locked control is used by another user."""
    session_ended: TextLike = "This session has ended."
    """Ephemeral rejection shown when a control on a finished mount is clicked anyway."""
    not_now: TextLike = "You can't do that right now."
    """Ephemeral rejection shown when an action's guard denies a press with no wording."""
    try_again_in: Callable[[float], TextLike] = _default_try_again_in
    """Denial wording when the guard said how long to wait; called with seconds."""
    working: TextLike = "Working…"
    """What a control with `BusySpec` says while its handler runs."""
    updates_paused: TextLike = "Live updates paused — press any control to resume."
    """Status shown before an interaction edit token expires and unattended refreshes pause."""
    session_expiring: TextLike = "This session is about to expire."
    """Status shown while an ephemeral session waits for an explicit renewal click."""
    continue_session: TextLike = "Continue Session"
    """Framework-owned action that renews an expiring ephemeral session."""
    changed_elsewhere: TextLike = "Someone else changed this while you were working. Try again."
    """Wording for a shared-state conflict: what the action read moved before it committed."""
    sent_privately: TextLike = "Sent by direct message."
    """Channel confirmation after a reply was delivered by direct message."""
    dm_unavailable: TextLike = (
        "This reply is too private for a channel, and your direct messages "
        "are closed. Run the command in a direct message, or allow direct messages and retry."
    )
    """Channel notice when a private reply cannot be delivered by direct message."""
    previous: TextLike = "Previous"
    next: TextLike = "Next"
    older: TextLike = "Older"
    newer: TextLike = "Newer"
    back: TextLike = "Back"
    home: TextLike = "Home"
    close: TextLike = "Close"
    undo: TextLike = "Undo"
    redo: TextLike = "Redo"
    on: TextLike = "On"
    off: TextLike = "Off"
    download: TextLike = "Download"
    confirm: TextLike = "Confirm"
    cancel: TextLike = "Cancel"
    apply: TextLike = "Apply"
    save: TextLike = "Save"
    unsaved: TextLike = "Unsaved changes"
    search: TextLike = "Search"
    no_results: TextLike = "No results"
    decided: Callable[[TextLike], TextLike] = _default_decided
    add: TextLike = "Add"
    edit: TextLike = "Edit"
    remove: TextLike = "Remove"
    move_up: TextLike = "Move up"
    move_down: TextLike = "Move down"
    review: TextLike = "Review"
    finish: TextLike = "Finish"
    continue_step: TextLike = "Continue"
    """Opens the current wizard step's form when more steps follow it."""
    step_progress: Callable[[int, int], TextLike] = _default_step_progress
    """A wizard's position; called with (step, steps), 1-based."""
    unanswered: TextLike = "Not answered yet"
    page_footer: Callable[[int, int], TextLike] = _default_page_footer
    """Small-text footer under paginated content; called with (page, pages), 1-based."""
    range_footer: Callable[[int, int], TextLike] = _default_range_footer
    """Visible 1-based item range for a source with known offsets."""
    approximate_total_footer: Callable[[int, int, int], TextLike] = _default_approximate_total_footer
    """Visible range and approximate source total."""
    total_range_footer: Callable[[int, int, int], TextLike] = _default_total_range_footer
    """Visible range and exact source total."""
    jump_to_page: TextLike = "Jump to a page"
    """Placeholder on the jump select a seekable paginator can offer."""
    page_option: Callable[[int], TextLike] = _default_page_option
    """One entry of that select; called with a 1-based page number."""
    waitlist: TextLike = "Waitlist"
    full: TextLike = "Full"
    slot_count: Callable[[int, int | None], TextLike] = _default_slot_count
    approve: TextLike = "Approve"
    withdraw: TextLike = "Withdraw"
    approved_count: Callable[[int, int], TextLike] = _default_approved_count
    form_invalid_boolean: TextLike = "Choose yes or no."
    form_required: TextLike = "This field is required."
    form_text_minimum: Callable[[int], TextLike] = _default_form_text_minimum
    form_text_maximum: Callable[[int], TextLike] = _default_form_text_maximum
    form_number_minimum: Callable[[int | float], TextLike] = _default_form_number_minimum
    form_number_maximum: Callable[[int | float], TextLike] = _default_form_number_maximum
    form_number_finite: TextLike = "Enter a finite number."
    form_duration_minimum: Callable[[int | float], TextLike] = _default_form_duration_minimum
    form_duration_maximum: Callable[[int | float], TextLike] = _default_form_duration_maximum
    form_date_minimum: Callable[[str], TextLike] = _default_form_date_minimum
    form_date_maximum: Callable[[str], TextLike] = _default_form_date_maximum
    form_time_minimum: Callable[[str], TextLike] = _default_form_time_minimum
    form_time_maximum: Callable[[str], TextLike] = _default_form_time_maximum
    form_datetime_minimum: Callable[[str], TextLike] = _default_form_datetime_minimum
    form_datetime_maximum: Callable[[str], TextLike] = _default_form_datetime_maximum
    form_scale_range: Callable[[int | float, int | float], TextLike] = _default_form_scale_range
    form_invalid_choice: TextLike = "Choose one of the available options."
    form_invalid_choices: TextLike = "Choose only from the available options."
    form_selection_minimum: Callable[[int], TextLike] = _default_form_selection_minimum
    form_selection_maximum: Callable[[int], TextLike] = _default_form_selection_maximum
    form_integer_format: TextLike = "Enter a whole number."
    form_number_format: TextLike = "Enter a number."
    form_duration_format: TextLike = "Enter a duration such as 30m, 12h, or 7d."
    form_date_format: TextLike = "Enter a date as YYYY-MM-DD."
    form_time_format: TextLike = "Enter a time as HH:MM."
    form_datetime_format: TextLike = "Enter a date and time as YYYY-MM-DD HH:MM."
    form_time_ambiguous: TextLike = (
        "This local time occurs twice in the selected timezone. Enter a date and time with an explicit UTC offset."
    )
    form_time_nonexistent: TextLike = "This local time does not exist in the selected timezone."
    form_timezone_offset_mismatch: TextLike = "The UTC offset does not match this local time in the selected timezone."
    form_scale_integer: Callable[[int | float, int | float], TextLike] = _default_form_scale_integer
    page_jump_unavailable: TextLike = "This pager is no longer available."
    page_jump_number: TextLike = "Page number"
    page_jump_range: Callable[[int], TextLike] = _default_page_jump_range
    tally_option: Callable[[TextLike, int], TextLike] = _default_tally_option

    agreement_participant: Callable[[str, TextLike], TextLike] = _default_agreement_participant
    collection_too_few: Callable[[int], TextLike] = _default_collection_too_few
    collection_too_many: Callable[[int], TextLike] = _default_collection_too_many
    multi_choice_select_options: TextLike = "Select options"
    multi_choice_too_few: Callable[[int], TextLike] = _default_multi_choice_too_few
    multi_choice_too_many: Callable[[int], TextLike] = _default_multi_choice_too_many
    multi_choice_unavailable: TextLike = "One or more selected options are no longer available."
    multi_choice_exclusive: Callable[[TextLike], TextLike] = _default_multi_choice_exclusive
    multi_choice_options: TextLike = "Options"

    dev_name: TextLike = "dev"
    dev_ui_name: TextLike = "ui"
    dev_help: TextLike = "Development-only operational diagnostics."
    dev_ui_help: TextLike = "Open the unified operational dashboard."
    dev_ui_tab_name: TextLike = "tab"
    dev_ui_tab_help: TextLike = "The dashboard tab to open first."
    dev_ui_choice_overview: TextLike = "overview"
    dev_ui_choice_roots: TextLike = "roots"
    dev_ui_choice_sessions: TextLike = "sessions"
    dev_ui_choice_routes: TextLike = "routes"
    dev_ui_choice_activity: TextLike = "activity"
    dev_ui_choice_queues: TextLike = "queues"
    dev_ui_choice_profile: TextLike = "profile"
    dev_ui_choice_persistence: TextLike = "persistence"


DEFAULT_CHROME = Chrome()
CHROME_CONTEXT = ContextKey[Chrome]("chrome")
LOCALIZATION_CONTEXT = ContextKey[Localization]("localization")


def localize_chrome(chrome: Chrome, localization: Localization) -> Chrome:
    """Resolve fixed labels now and wrap dynamic labels with the same localization.

    Callbacks still run when their counts, ranges, or selected labels are known.
    """
    return Chrome(
        dev_name=resolve_text(chrome.dev_name, localization).content,
        dev_ui_name=resolve_text(chrome.dev_ui_name, localization).content,
        dev_help=resolve_text(chrome.dev_help, localization).content,
        dev_ui_help=resolve_text(chrome.dev_ui_help, localization).content,
        dev_ui_tab_name=resolve_text(chrome.dev_ui_tab_name, localization).content,
        dev_ui_tab_help=resolve_text(chrome.dev_ui_tab_help, localization).content,
        dev_ui_choice_overview=resolve_text(chrome.dev_ui_choice_overview, localization).content,
        dev_ui_choice_roots=resolve_text(chrome.dev_ui_choice_roots, localization).content,
        dev_ui_choice_sessions=resolve_text(chrome.dev_ui_choice_sessions, localization).content,
        dev_ui_choice_routes=resolve_text(chrome.dev_ui_choice_routes, localization).content,
        dev_ui_choice_activity=resolve_text(chrome.dev_ui_choice_activity, localization).content,
        dev_ui_choice_queues=resolve_text(chrome.dev_ui_choice_queues, localization).content,
        dev_ui_choice_profile=resolve_text(chrome.dev_ui_choice_profile, localization).content,
        dev_ui_choice_persistence=resolve_text(chrome.dev_ui_choice_persistence, localization).content,
        agreement_participant=lambda mark, display: (
            resolve_text(chrome.agreement_participant(mark, display), localization).content
        ),
        collection_too_few=lambda minimum: resolve_text(chrome.collection_too_few(minimum), localization).content,
        collection_too_many=lambda maximum: resolve_text(chrome.collection_too_many(maximum), localization).content,
        multi_choice_select_options=resolve_text(chrome.multi_choice_select_options, localization).content,
        multi_choice_too_few=lambda minimum: resolve_text(chrome.multi_choice_too_few(minimum), localization).content,
        multi_choice_too_many=lambda maximum: resolve_text(chrome.multi_choice_too_many(maximum), localization).content,
        multi_choice_unavailable=resolve_text(chrome.multi_choice_unavailable, localization).content,
        multi_choice_exclusive=lambda group_label: (
            resolve_text(chrome.multi_choice_exclusive(group_label), localization).content
        ),
        multi_choice_options=resolve_text(chrome.multi_choice_options, localization).content,
        form_invalid_boolean=resolve_text(chrome.form_invalid_boolean, localization).content,
        form_required=resolve_text(chrome.form_required, localization).content,
        form_text_minimum=lambda minimum: resolve_text(chrome.form_text_minimum(minimum), localization).content,
        form_text_maximum=lambda maximum: resolve_text(chrome.form_text_maximum(maximum), localization).content,
        form_number_minimum=lambda minimum: resolve_text(chrome.form_number_minimum(minimum), localization).content,
        form_number_maximum=lambda maximum: resolve_text(chrome.form_number_maximum(maximum), localization).content,
        form_number_finite=resolve_text(chrome.form_number_finite, localization).content,
        form_duration_minimum=lambda minimum: resolve_text(chrome.form_duration_minimum(minimum), localization).content,
        form_duration_maximum=lambda maximum: resolve_text(chrome.form_duration_maximum(maximum), localization).content,
        form_date_minimum=lambda value: resolve_text(chrome.form_date_minimum(value), localization).content,
        form_date_maximum=lambda value: resolve_text(chrome.form_date_maximum(value), localization).content,
        form_time_minimum=lambda value: resolve_text(chrome.form_time_minimum(value), localization).content,
        form_time_maximum=lambda value: resolve_text(chrome.form_time_maximum(value), localization).content,
        form_datetime_minimum=lambda value: resolve_text(chrome.form_datetime_minimum(value), localization).content,
        form_datetime_maximum=lambda value: resolve_text(chrome.form_datetime_maximum(value), localization).content,
        form_scale_range=lambda minimum, maximum: (
            resolve_text(chrome.form_scale_range(minimum, maximum), localization).content
        ),
        form_invalid_choice=resolve_text(chrome.form_invalid_choice, localization).content,
        form_invalid_choices=resolve_text(chrome.form_invalid_choices, localization).content,
        form_selection_minimum=lambda minimum: (
            resolve_text(chrome.form_selection_minimum(minimum), localization).content
        ),
        form_selection_maximum=lambda maximum: (
            resolve_text(chrome.form_selection_maximum(maximum), localization).content
        ),
        form_integer_format=resolve_text(chrome.form_integer_format, localization).content,
        form_number_format=resolve_text(chrome.form_number_format, localization).content,
        form_duration_format=resolve_text(chrome.form_duration_format, localization).content,
        form_date_format=resolve_text(chrome.form_date_format, localization).content,
        form_time_format=resolve_text(chrome.form_time_format, localization).content,
        form_datetime_format=resolve_text(chrome.form_datetime_format, localization).content,
        form_time_ambiguous=resolve_text(chrome.form_time_ambiguous, localization).content,
        form_time_nonexistent=resolve_text(chrome.form_time_nonexistent, localization).content,
        form_timezone_offset_mismatch=resolve_text(chrome.form_timezone_offset_mismatch, localization).content,
        form_scale_integer=lambda minimum, maximum: (
            resolve_text(chrome.form_scale_integer(minimum, maximum), localization).content
        ),
        page_jump_unavailable=resolve_text(chrome.page_jump_unavailable, localization).content,
        page_jump_number=resolve_text(chrome.page_jump_number, localization).content,
        page_jump_range=lambda pages: resolve_text(chrome.page_jump_range(pages), localization).content,
        tally_option=lambda label, count: resolve_text(chrome.tally_option(label, count), localization).content,
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

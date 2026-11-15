"""Application-owned translations for framework controls and validation."""

from math import ceil

import squid_ui as ui
from squid_i18n import NUMBER, md_tr, tr


def _try_again_in(seconds: float) -> ui.text.Text:
    """Round a guard's remaining cooldown up to whole seconds before wording it."""
    whole = max(1, ceil(seconds))
    return md_tr(t"bot-ui-try-again-in-seconds = Try again in {whole} seconds.")


def _chrome_step_progress(step: int, steps: int) -> ui.TextLike:
    return md_tr(t"ui-chrome-step-progress = Step {step} of {steps}")


def _chrome_range_footer(first: int, last: int) -> ui.TextLike:
    return md_tr(t"ui-chrome-range-footer = {first}\N{EN DASH}{last}")


def _chrome_approximate_total_footer(first: int, last: int, total: int) -> ui.TextLike:
    return md_tr(t"ui-chrome-approximate-total-footer = {first}\N{EN DASH}{last} of ~{total}")


def _chrome_total_range_footer(first: int, last: int, total: int) -> ui.TextLike:
    return md_tr(t"ui-chrome-total-range-footer = {first}\N{EN DASH}{last} of {total}")


def _chrome_page_option(page: int) -> ui.TextLike:
    return md_tr(t"ui-chrome-page-option = Page {page}")


def _chrome_slot_count(count: int, capacity: int | None) -> ui.TextLike:
    return (
        md_tr(t"ui-chrome-slot-count = {count}")
        if capacity is None
        else md_tr(t"ui-chrome-slot-capacity = {count}/{capacity}")
    )


def _chrome_approved_count(count: int, total: int) -> ui.TextLike:
    return md_tr(t"ui-chrome-approved-count = Approved: {count}/{total}")


def _form_text_minimum(minimum: int) -> ui.TextLike:
    return md_tr(t"ui-form-text-minimum = Enter at least {minimum} characters.")


def _form_text_maximum(maximum: int) -> ui.TextLike:
    return md_tr(t"ui-form-text-maximum = Enter no more than {maximum} characters.")


def _form_number_minimum(minimum: int | float) -> ui.TextLike:
    return md_tr(t"ui-form-number-minimum = Enter a value of at least {minimum}.")


def _form_number_maximum(maximum: int | float) -> ui.TextLike:
    return md_tr(t"ui-form-number-maximum = Enter a value no greater than {maximum}.")


def _form_duration_minimum(minimum: int | float) -> ui.TextLike:
    return md_tr(t"ui-form-duration-minimum = Enter a duration of at least {minimum} seconds.")


def _form_duration_maximum(maximum: int | float) -> ui.TextLike:
    return md_tr(t"ui-form-duration-maximum = Enter a duration no longer than {maximum} seconds.")


def _form_date_minimum(value: str) -> ui.TextLike:
    return md_tr(t"ui-form-date-minimum = Enter a date on or after {value}.")


def _form_date_maximum(value: str) -> ui.TextLike:
    return md_tr(t"ui-form-date-maximum = Enter a date on or before {value}.")


def _form_time_minimum(value: str) -> ui.TextLike:
    return md_tr(t"ui-form-time-minimum = Enter a time at or after {value}.")


def _form_time_maximum(value: str) -> ui.TextLike:
    return md_tr(t"ui-form-time-maximum = Enter a time at or before {value}.")


def _form_datetime_minimum(value: str) -> ui.TextLike:
    return md_tr(t"ui-form-datetime-minimum = Enter a date and time on or after {value}.")


def _form_datetime_maximum(value: str) -> ui.TextLike:
    return md_tr(t"ui-form-datetime-maximum = Enter a date and time on or before {value}.")


def _form_scale_range(minimum: int | float, maximum: int | float) -> ui.TextLike:
    return md_tr(t"ui-form-scale-range = Choose a value from {minimum} to {maximum}.")


def _form_selection_minimum(minimum: int) -> ui.TextLike:
    return md_tr(t"ui-form-selection-minimum = Choose at least {minimum} options.")


def _form_selection_maximum(maximum: int) -> ui.TextLike:
    return md_tr(t"ui-form-selection-maximum = Choose no more than {maximum} options.")


def _form_scale_integer(minimum: int | float, maximum: int | float) -> ui.TextLike:
    return md_tr(t"ui-form-scale-integer = Choose a whole number from {minimum} to {maximum}.")


def _page_jump_range(pages: int) -> ui.TextLike:
    return md_tr(t"ui-page-jump-range = Enter a page from 1 to {pages}.")


def _tally_option(label: ui.TextLike, count: int) -> ui.TextLike:
    return md_tr(t"ui-tally-option = {label} — {NUMBER(count)}")


def _agreement_participant(mark: str, display: ui.TextLike) -> ui.TextLike:
    return tr(t"widgets-agreement-participant = {mark} {display}")


def _collection_too_few(minimum: int) -> ui.TextLike:
    return tr(
        t"\n                    widgets-collection-too-few = {{ {NUMBER(minimum)} ->\n                        [one] Add at least {NUMBER(minimum)} entry.\n                       *[other] Add at least {NUMBER(minimum)} entries.\n                    }}\n                    "
    )


def _collection_too_many(maximum: int) -> ui.TextLike:
    return tr(
        t"\n                    widgets-collection-too-many = {{ {NUMBER(maximum)} ->\n                        [one] Keep no more than {NUMBER(maximum)} entry.\n                       *[other] Keep no more than {NUMBER(maximum)} entries.\n                    }}\n                    "
    )


def _multi_choice_too_few(minimum: int) -> ui.TextLike:
    return tr(
        t"\n                    widgets-multi-choice-too-few = {{ {NUMBER(minimum)} ->\n                        [one] Select at least {NUMBER(minimum)} option.\n                       *[other] Select at least {NUMBER(minimum)} options.\n                    }}\n                    "
    )


def _multi_choice_too_many(maximum: int) -> ui.TextLike:
    return tr(
        t"\n                    widgets-multi-choice-too-many = {{ {NUMBER(maximum)} ->\n                        [one] Select no more than {NUMBER(maximum)} option.\n                       *[other] Select no more than {NUMBER(maximum)} options.\n                    }}\n                    "
    )


def _multi_choice_exclusive(group_label: ui.TextLike) -> ui.TextLike:
    return tr(t"widgets-multi-choice-exclusive = {group_label} cannot be combined with an exclusive group.")


CHROME = ui.chrome.Chrome(
    dev_name=tr(t"command-dev-name = dev"),
    dev_ui_name=tr(t"command-dev-ui-name = ui"),
    dev_help=tr(t"command-dev-help = Development-only operational diagnostics."),
    dev_ui_help=tr(t"command-dev-ui-help = Open the unified operational dashboard."),
    dev_ui_tab_name=tr(t"command-dev-ui-tab-name = tab"),
    dev_ui_tab_help=tr(t"command-dev-ui-tab-help = The dashboard tab to open first."),
    dev_ui_choice_overview=tr(t"command-dev-ui-choice-overview = overview"),
    dev_ui_choice_roots=tr(t"command-dev-ui-choice-roots = roots"),
    dev_ui_choice_sessions=tr(t"command-dev-ui-choice-sessions = sessions"),
    dev_ui_choice_routes=tr(t"command-dev-ui-choice-routes = routes"),
    dev_ui_choice_activity=tr(t"command-dev-ui-choice-activity = activity"),
    dev_ui_choice_queues=tr(t"command-dev-ui-choice-queues = queues"),
    dev_ui_choice_profile=tr(t"command-dev-ui-choice-profile = profile"),
    dev_ui_choice_persistence=tr(t"command-dev-ui-choice-persistence = persistence"),
    agreement_participant=_agreement_participant,
    collection_too_few=_collection_too_few,
    collection_too_many=_collection_too_many,
    multi_choice_select_options=tr(t"widgets-multi-choice-select-options = Select options"),
    multi_choice_too_few=_multi_choice_too_few,
    multi_choice_too_many=_multi_choice_too_many,
    multi_choice_unavailable=tr(
        t"widgets-multi-choice-unavailable = One or more selected options are no longer available."
    ),
    multi_choice_exclusive=_multi_choice_exclusive,
    multi_choice_options=tr(t"widgets-multi-choice-options = Options"),
    form_invalid_boolean=tr(t"ui-form-invalid-boolean = Choose yes or no."),
    form_required=tr(t"ui-form-required = This field is required."),
    form_text_minimum=_form_text_minimum,
    form_text_maximum=_form_text_maximum,
    form_number_minimum=_form_number_minimum,
    form_number_maximum=_form_number_maximum,
    form_number_finite=tr(t"ui-form-number-finite = Enter a finite number."),
    form_duration_minimum=_form_duration_minimum,
    form_duration_maximum=_form_duration_maximum,
    form_date_minimum=_form_date_minimum,
    form_date_maximum=_form_date_maximum,
    form_time_minimum=_form_time_minimum,
    form_time_maximum=_form_time_maximum,
    form_datetime_minimum=_form_datetime_minimum,
    form_datetime_maximum=_form_datetime_maximum,
    form_scale_range=_form_scale_range,
    form_invalid_choice=tr(t"ui-form-invalid-choice = Choose one of the available options."),
    form_invalid_choices=tr(t"ui-form-invalid-choices = Choose only from the available options."),
    form_selection_minimum=_form_selection_minimum,
    form_selection_maximum=_form_selection_maximum,
    form_integer_format=tr(t"ui-form-integer-format = Enter a whole number."),
    form_number_format=tr(t"ui-form-number-format = Enter a number."),
    form_duration_format=tr(t"ui-form-duration-format = Enter a duration such as 30m, 12h, or 7d."),
    form_date_format=tr(t"ui-form-date-format = Enter a date as YYYY-MM-DD."),
    form_time_format=tr(t"ui-form-time-format = Enter a time as HH:MM."),
    form_datetime_format=tr(t"ui-form-datetime-format = Enter a date and time as YYYY-MM-DD HH:MM."),
    form_time_ambiguous=tr(
        t"ui-form-time-ambiguous = This local time occurs twice in the selected timezone. Enter a date and time with an explicit UTC offset."
    ),
    form_time_nonexistent=tr(t"ui-form-time-nonexistent = This local time does not exist in the selected timezone."),
    form_timezone_offset_mismatch=tr(
        t"ui-form-timezone-offset-mismatch = The UTC offset does not match this local time in the selected timezone."
    ),
    form_scale_integer=_form_scale_integer,
    page_jump_unavailable=tr(t"ui-page-jump-unavailable = This pager is no longer available."),
    page_jump_number=tr(t"ui-page-jump-number = Page number"),
    page_jump_range=_page_jump_range,
    tally_option=_tally_option,
    and_n_more=lambda count: md_tr(t"bot-ui-and-more = …and {count} more."),
    not_yours=tr(t"ui-list-controls-not-owned = These list controls belong to someone else."),
    session_ended=tr(t"bot-ui-this-session-has-ended = This session has ended."),
    not_now=tr(t"bot-ui-you-can-t-do-that-right-now = You can't do that right now."),
    try_again_in=_try_again_in,
    working=tr(t"bot-ui-working = Working…"),
    updates_paused=tr(
        t"bot-ui-live-updates-paused-press-any-control-to = Live updates paused — press any control to resume."
    ),
    session_expiring=tr(t"bot-ui-this-session-is-about-to-expire = This session is about to expire."),
    continue_session=tr(t"bot-ui-continue-session = Continue Session"),
    sent_privately=tr(t"bot-ui-sent-by-direct-message = Sent by direct message."),
    dm_unavailable=tr(
        t"bot-ui-this-reply-is-too-private-for-a = This reply is too private for a channel, and your "
        t"direct messages are closed. Run the command in a direct message, or allow direct messages and "
        t"retry."
    ),
    previous=tr(t"bot-ui-previous = Previous"),
    next=tr(t"bot-ui-next = Next"),
    back=tr(t"bot-ui-back = Back"),
    home=tr(t"bot-ui-home = Home"),
    close=tr(t"bot-ui-close = Close"),
    on=tr(t"bot-ui-on = On"),
    off=tr(t"bot-ui-off = Off"),
    download=tr(t"bot-ui-download = Download"),
    confirm=tr(t"bot-ui-confirm = Confirm"),
    cancel=tr(t"bot-ui-cancel = Cancel"),
    apply=tr(t"bot-ui-apply = Apply"),
    save=tr(t"bot-ui-save = Save"),
    unsaved=tr(t"bot-ui-unsaved-changes = Unsaved changes"),
    search=tr(t"bot-ui-search = Search"),
    no_results=tr(t"bot-ui-no-results = No results"),
    decided=lambda label: md_tr(t"bot-ui-you-chose = You chose {label}."),
    add=tr(t"bot-ui-add = Add"),
    edit=tr(t"bot-ui-edit = Edit"),
    remove=tr(t"bot-ui-remove = Remove"),
    move_up=tr(t"bot-ui-move-up = Move up"),
    move_down=tr(t"bot-ui-move-down = Move down"),
    review=tr(t"bot-ui-review = Review"),
    finish=tr(t"bot-ui-finish = Finish"),
    unanswered=tr(t"bot-ui-not-answered-yet = Not answered yet"),
    page_footer=lambda page, pages: md_tr(t"bot-ui-page-of = Page {page} of {pages}"),
    changed_elsewhere=tr(t"ui-chrome-changed-elsewhere = Someone else changed this while you were working. Try again."),
    older=tr(t"ui-chrome-older = Older"),
    newer=tr(t"ui-chrome-newer = Newer"),
    undo=tr(t"ui-chrome-undo = Undo"),
    redo=tr(t"ui-chrome-redo = Redo"),
    continue_step=tr(t"ui-chrome-continue = Continue"),
    step_progress=_chrome_step_progress,
    range_footer=_chrome_range_footer,
    approximate_total_footer=_chrome_approximate_total_footer,
    total_range_footer=_chrome_total_range_footer,
    jump_to_page=tr(t"ui-chrome-jump-to-page = Jump to a page"),
    page_option=_chrome_page_option,
    waitlist=tr(t"ui-chrome-waitlist = Waitlist"),
    full=tr(t"ui-chrome-full = Full"),
    slot_count=_chrome_slot_count,
    approve=tr(t"ui-chrome-approve = Approve"),
    withdraw=tr(t"ui-chrome-withdraw = Withdraw"),
    approved_count=_chrome_approved_count,
)

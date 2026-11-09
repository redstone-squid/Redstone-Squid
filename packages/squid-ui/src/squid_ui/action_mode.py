"""Dispatch policy values shared by authored actions and serialized scenes."""

from enum import StrEnum


class ActionMode(StrEnum):
    """The dispatch policy requested by an interactive control.

    Mounted frontends track a render generation so a press can be recognized as coming
    from an older display. EXCLUSIVE serializes actions and rejects stale presses;
    REBASE serializes them but resolves the current binding before invoking it.
    IMMEDIATE skips that lock and generation check. PARALLEL_READ also skips them,
    but runs the handler in a read-only state transaction.
    """

    EXCLUSIVE = "exclusive"
    REBASE = "rebase"
    PARALLEL_READ = "parallel_read"
    IMMEDIATE = "immediate"

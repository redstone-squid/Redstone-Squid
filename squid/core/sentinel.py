"""Shared markers for omitted values and unanswered prompts. Compare by identity."""

UNSET = sentinel("UNSET")
"""Leave a patch field unchanged; None may instead clear its value."""

MISSING = sentinel("MISSING")
"""No value is available."""

DEFAULT = sentinel("DEFAULT")
"""Use the caller's default selection rather than an explicit value."""

NOT_ASKED = sentinel("NOT_ASKED")
"""The consent question was not presented, and the user has already been told why."""

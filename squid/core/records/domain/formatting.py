"""Compatibility imports for the shared catalogue title grammar."""

from typing import Protocol

from squid.core.catalogue.domain.titles import DoorCategory, ExtenderCategory, FormattedTitle, RulesTitleFormatter
from squid.core.records.domain.models import RecordCriterion

__all__ = ["DoorCategory", "ExtenderCategory", "FormattedTitle", "RulesTitleFormatter", "TitleFormatter"]


class TitleFormatter(Protocol):
    """A rules revision-specific formatter used when snapshotting record results."""

    def format_door(self, category: DoorCategory) -> FormattedTitle:
        """Format a piston-door category."""
        ...

    def format_extender(self, category: ExtenderCategory) -> FormattedTitle:
        """Format a piston-extender category."""
        ...

    def format_record(self, criterion: RecordCriterion, category: FormattedTitle) -> FormattedTitle:
        """Format a record name from its criterion and category."""
        ...

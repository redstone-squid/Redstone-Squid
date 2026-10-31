"""Compatibility imports for the shared catalogue title grammar."""

from typing import Protocol

from squid.catalogue.domain.titles import DoorCategory, ExtenderCategory, FormattedTitle, RulesTitleFormatter
from squid.records.domain.models import RecordClass

__all__ = ["DoorCategory", "ExtenderCategory", "FormattedTitle", "RulesTitleFormatter", "TitleFormatter"]


class TitleFormatter(Protocol):
    """A ruleset-specific formatter used when snapshotting record results."""

    def format_door(self, category: DoorCategory) -> FormattedTitle:
        """Format a piston-door category."""
        ...

    def format_extender(self, category: ExtenderCategory) -> FormattedTitle:
        """Format a piston-extender category."""
        ...

    def format_record(self, record_class: RecordClass, category: FormattedTitle) -> FormattedTitle:
        """Prefix a category with its record class."""
        ...

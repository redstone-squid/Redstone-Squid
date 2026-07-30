"""Compatibility imports for the shared catalogue title grammar."""

from typing import Protocol

from squid.catalogue.domain.titles import CategoryText, DoorCategory, ExtenderCategory, RulesTitleFormatter
from squid.records.domain.models import RecordClass

__all__ = ["CategoryText", "DoorCategory", "ExtenderCategory", "RulesTitleFormatter", "TitleFormatter"]


class TitleFormatter(Protocol):
    """A ruleset-specific formatter used when snapshotting record results."""

    def format_door(self, category: DoorCategory) -> CategoryText:
        """Format a piston-door category."""
        ...

    def format_extender(self, category: ExtenderCategory) -> CategoryText:
        """Format a piston-extender category."""
        ...

    def format_record(self, record_class: RecordClass, category: CategoryText) -> CategoryText:
        """Prefix a category with its record class."""
        ...

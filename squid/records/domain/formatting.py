"""The record title formatter contract, over the re-exported catalogue title grammar."""

from typing import Protocol

from squid.catalogue.domain.titles import CategoryText, DoorCategory, ExtenderCategory, RulesTitleFormatter
from squid.records.domain.models import RecordClass

__all__ = ["CategoryText", "DoorCategory", "ExtenderCategory", "RulesTitleFormatter", "TitleFormatter"]


class TitleFormatter(Protocol):
    """A ruleset-specific formatter used when snapshotting record results.

    A term the ruleset does not recognize is reported in the returned text's diagnostics rather
    than raised, so a title is always produced.
    """

    def format_door(self, category: DoorCategory) -> CategoryText: ...

    def format_extender(self, category: ExtenderCategory) -> CategoryText: ...

    def format_record(self, record_class: RecordClass, category: CategoryText) -> CategoryText:
        """Prefix the category title with the record class, keeping its subtitle and diagnostics."""
        ...

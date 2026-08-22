"""Reusable, frontend-neutral state-machine patterns."""

from squid_layouts.patterns.collection import (
    CollectionChangeHandler,
    CollectionEditor,
    CollectionEntry,
    CollectionState,
)
from squid_layouts.patterns.commit import CommitPolicy
from squid_layouts.patterns.decision import Decision, DecisionHandler, DecisionOption, DecisionState, confirm
from squid_layouts.patterns.menu import Menu, MenuEntry, MenuState
from squid_layouts.patterns.multichoice import (
    MultiChoiceCommitHandler,
    MultiChoiceGroup,
    MultiChoicePanel,
    MultiChoiceState,
)
from squid_layouts.patterns.ranked import RankedEntry, RankedList, RankedListState
from squid_layouts.patterns.shells import (
    ComponentShell,
    Pattern,
    PatternControls,
    PatternEvent,
    PatternHandler,
    PatternRoute,
    RouteBuilder,
    RouterShell,
)
from squid_layouts.patterns.source_ranked import SourceRankedList
from squid_layouts.patterns.tabs import Tab, Tabs, TabsState
from squid_layouts.patterns.wizard import (
    Wizard,
    WizardAnswer,
    WizardAnswers,
    WizardFinishHandler,
    WizardState,
    WizardStep,
)

__all__ = [
    "CollectionChangeHandler",
    "CollectionEditor",
    "CollectionEntry",
    "CollectionState",
    "CommitPolicy",
    "ComponentShell",
    "Decision",
    "DecisionHandler",
    "DecisionOption",
    "DecisionState",
    "Menu",
    "MenuEntry",
    "MenuState",
    "MultiChoiceCommitHandler",
    "MultiChoiceGroup",
    "MultiChoicePanel",
    "MultiChoiceState",
    "Pattern",
    "PatternControls",
    "PatternEvent",
    "PatternHandler",
    "PatternRoute",
    "RankedEntry",
    "RankedList",
    "RankedListState",
    "RouteBuilder",
    "RouterShell",
    "SourceRankedList",
    "Tab",
    "Tabs",
    "TabsState",
    "Wizard",
    "WizardAnswer",
    "WizardAnswers",
    "WizardFinishHandler",
    "WizardState",
    "WizardStep",
    "confirm",
]

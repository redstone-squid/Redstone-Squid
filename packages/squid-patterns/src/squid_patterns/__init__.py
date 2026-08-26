"""Reusable, frontend-neutral state-machine patterns."""

from squid_patterns import guards
from squid_patterns.agreement import Agreement, AgreementParticipant, AgreementResolveHandler
from squid_patterns.browser import Browser, BrowserDetail, BrowserOpenHandler, BrowserOverview
from squid_patterns.collection import (
    CollectionChangeHandler,
    CollectionEditor,
    CollectionEntry,
    CollectionState,
)
from squid_patterns.commit import CommitMode
from squid_patterns.decision import Decision, DecisionHandler, DecisionOption, DecisionState, confirm
from squid_patterns.editor import (
    Editor,
    EditorCommitHandler,
    EditorSection,
    EditorSectionState,
    EditorState,
    EditorValues,
)
from squid_patterns.grid import GridCell
from squid_patterns.lookup import Lookup, LookupPickHandler, LookupSearch
from squid_patterns.menu import Menu, MenuEntry, MenuState
from squid_patterns.multichoice import (
    MultiChoiceCommitHandler,
    MultiChoiceGroup,
    MultiChoicePanel,
    MultiChoiceState,
)
from squid_patterns.ranked import RankedEntry, RankedList, RankedListState
from squid_patterns.roster import (
    RosterEntry,
    RosterGroup,
    RosterOverflow,
    RosterPlacement,
    RosterSlot,
    RosterStatus,
    place_roster,
)
from squid_patterns.shells import (
    ComponentShell,
    Pattern,
    PatternControls,
    PatternEvent,
    PatternHandler,
    PatternRoute,
    RouteBuilder,
    RouterShell,
)
from squid_patterns.source_ranked import SourceRankedList
from squid_patterns.tabs import Tab, Tabs, TabsState
from squid_patterns.tally import TallyOption
from squid_patterns.wizard import (
    REVIEW_STEP,
    Wizard,
    WizardAnswer,
    WizardAnswers,
    WizardFinishHandler,
    WizardReview,
    WizardState,
    WizardStep,
)

__all__ = [
    "REVIEW_STEP",
    "Agreement",
    "AgreementParticipant",
    "AgreementResolveHandler",
    "Browser",
    "BrowserDetail",
    "BrowserOpenHandler",
    "BrowserOverview",
    "CollectionChangeHandler",
    "CollectionEditor",
    "CollectionEntry",
    "CollectionState",
    "CommitMode",
    "ComponentShell",
    "Decision",
    "DecisionHandler",
    "DecisionOption",
    "DecisionState",
    "Editor",
    "EditorCommitHandler",
    "EditorSection",
    "EditorSectionState",
    "EditorState",
    "EditorValues",
    "GridCell",
    "Lookup",
    "LookupPickHandler",
    "LookupSearch",
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
    "RosterEntry",
    "RosterGroup",
    "RosterOverflow",
    "RosterPlacement",
    "RosterSlot",
    "RosterStatus",
    "RouteBuilder",
    "RouterShell",
    "SourceRankedList",
    "Tab",
    "Tabs",
    "TabsState",
    "TallyOption",
    "Wizard",
    "WizardAnswer",
    "WizardAnswers",
    "WizardFinishHandler",
    "WizardReview",
    "WizardState",
    "WizardStep",
    "confirm",
    "guards",
    "place_roster",
]

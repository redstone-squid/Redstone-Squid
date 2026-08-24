"""Reusable, frontend-neutral state-machine patterns."""

from squid_layouts.patterns.agreement import Agreement, AgreementParticipant, AgreementResolveHandler
from squid_layouts.patterns.browser import Browser, BrowserDetail, BrowserOpenHandler, BrowserOverview
from squid_layouts.patterns.collection import (
    CollectionChangeHandler,
    CollectionEditor,
    CollectionEntry,
    CollectionState,
)
from squid_layouts.patterns.commit import CommitPolicy
from squid_layouts.patterns.decision import Decision, DecisionHandler, DecisionOption, DecisionState, confirm
from squid_layouts.patterns.editor import (
    Editor,
    EditorCommitHandler,
    EditorSection,
    EditorSectionState,
    EditorState,
    EditorValues,
)
from squid_layouts.patterns.grid import GridCell
from squid_layouts.patterns.lookup import Lookup, LookupPickHandler, LookupSearch
from squid_layouts.patterns.menu import Menu, MenuEntry, MenuState
from squid_layouts.patterns.multichoice import (
    MultiChoiceCommitHandler,
    MultiChoiceGroup,
    MultiChoicePanel,
    MultiChoiceState,
)
from squid_layouts.patterns.ranked import RankedEntry, RankedList, RankedListState
from squid_layouts.patterns.roster import (
    RosterEntry,
    RosterGroup,
    RosterOverflow,
    RosterPlacement,
    RosterSlot,
    RosterStatus,
    place_roster,
)
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
from squid_layouts.patterns.tally import TallyOption
from squid_layouts.patterns.wizard import (
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
    "CommitPolicy",
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
    "place_roster",
]

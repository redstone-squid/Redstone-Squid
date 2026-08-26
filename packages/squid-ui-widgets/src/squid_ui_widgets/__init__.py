"""Reusable, frontend-neutral state-machine patterns."""

from squid_ui_widgets import guards
from squid_ui_widgets.agreement import Agreement, AgreementParticipant, AgreementResolveHandler
from squid_ui_widgets.browser import Browser, BrowserDetail, BrowserOpenHandler, BrowserOverview
from squid_ui_widgets.collection import (
    CollectionChangeHandler,
    CollectionEditor,
    CollectionEntry,
    CollectionState,
)
from squid_ui_widgets.commit import CommitMode
from squid_ui_widgets.decision import Decision, DecisionHandler, DecisionOption, DecisionState, confirm
from squid_ui_widgets.editor import (
    Editor,
    EditorCommitHandler,
    EditorSection,
    EditorSectionState,
    EditorState,
    EditorValues,
)
from squid_ui_widgets.grid import GridCell
from squid_ui_widgets.lookup import Lookup, LookupPickHandler, LookupSearch
from squid_ui_widgets.menu import Menu, MenuEntry, MenuState
from squid_ui_widgets.multichoice import (
    MultiChoiceCommitHandler,
    MultiChoiceGroup,
    MultiChoicePanel,
    MultiChoiceState,
)
from squid_ui_widgets.ranked import RankedEntry, RankedList, RankedListState
from squid_ui_widgets.roster import (
    RosterEntry,
    RosterGroup,
    RosterOverflow,
    RosterPlacement,
    RosterSlot,
    RosterStatus,
    place_roster,
)
from squid_ui_widgets.shells import (
    ComponentShell,
    Pattern,
    PatternControls,
    PatternEvent,
    PatternHandler,
    PatternRoute,
    RouteBuilder,
    RouterShell,
)
from squid_ui_widgets.source_ranked import SourceRankedList
from squid_ui_widgets.tabs import Tab, Tabs, TabsState
from squid_ui_widgets.tally import TallyOption
from squid_ui_widgets.wizard import (
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

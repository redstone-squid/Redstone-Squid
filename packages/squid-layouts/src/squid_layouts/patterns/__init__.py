"""Reusable, frontend-neutral state-machine patterns."""

from squid_layouts.patterns.menu import Menu, MenuEntry, MenuState
from squid_layouts.patterns.multichoice import MultiChoiceGroup, MultiChoicePanel, MultiChoiceState
from squid_layouts.patterns.ranked import RankedEntry, RankedList, RankedListState
from squid_layouts.patterns.shells import ComponentShell, PatternEvent, PatternRoute, RouterShell
from squid_layouts.patterns.tabs import Tab, Tabs, TabsState
from squid_layouts.patterns.wizard import Wizard, WizardAnswer, WizardAnswers, WizardState, WizardStep

__all__ = [
    "ComponentShell",
    "Menu",
    "MenuEntry",
    "MenuState",
    "MultiChoiceGroup",
    "MultiChoicePanel",
    "MultiChoiceState",
    "PatternEvent",
    "PatternRoute",
    "RankedEntry",
    "RankedList",
    "RankedListState",
    "RouterShell",
    "Tab",
    "Tabs",
    "TabsState",
    "Wizard",
    "WizardAnswer",
    "WizardAnswers",
    "WizardState",
    "WizardStep",
]

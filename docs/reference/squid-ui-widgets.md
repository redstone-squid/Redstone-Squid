# squid-ui-widgets

Frontend-neutral application patterns: each widget is a reusable state machine plus a portable
rendering, usable on any squid-ui target.

```python
import squid_ui_widgets as sw
```

## Wizards and editors

::: squid_ui_widgets.Wizard

::: squid_ui_widgets.WizardStep

::: squid_ui_widgets.WizardAnswer

::: squid_ui_widgets.WizardAnswers

::: squid_ui_widgets.WizardReview

::: squid_ui_widgets.WizardState

::: squid_ui_widgets.WizardFinishHandler

::: squid_ui_widgets.REVIEW_STEP

::: squid_ui_widgets.Editor

::: squid_ui_widgets.EditorSection

::: squid_ui_widgets.EditorSectionState

::: squid_ui_widgets.EditorState

::: squid_ui_widgets.EditorValues

::: squid_ui_widgets.EditorCommitHandler

::: squid_ui_widgets.CommitMode

::: squid_ui_widgets.CollectionEditor

::: squid_ui_widgets.CollectionEntry

::: squid_ui_widgets.CollectionState

::: squid_ui_widgets.CollectionChangeHandler

## Menus, tabs, and browsing

::: squid_ui_widgets.Menu

::: squid_ui_widgets.MenuEntry

::: squid_ui_widgets.MenuState

::: squid_ui_widgets.Tabs

::: squid_ui_widgets.Tab

::: squid_ui_widgets.TabsState

::: squid_ui_widgets.Browser

::: squid_ui_widgets.BrowserOverview

::: squid_ui_widgets.BrowserDetail

::: squid_ui_widgets.BrowserOpenHandler

::: squid_ui_widgets.SearchPicker

::: squid_ui_widgets.SearchProvider

::: squid_ui_widgets.SearchPickHandler

::: squid_ui_widgets.LoadingCopy

::: squid_ui_widgets.DEFAULT_LOADING_COPY

## Shared machine values

::: squid_ui_widgets.MachineKeySegment

::: squid_ui_widgets.PageDirection

::: squid_ui_widgets.PagePosition

## Decisions and votes

::: squid_ui_widgets.Decision

::: squid_ui_widgets.DecisionOption

::: squid_ui_widgets.DecisionState

::: squid_ui_widgets.DecisionHandler

::: squid_ui_widgets.confirm

::: squid_ui_widgets.MultiChoice

::: squid_ui_widgets.MultiChoiceGroup

::: squid_ui_widgets.MultiChoiceState

::: squid_ui_widgets.MultiChoiceCommitHandler

::: squid_ui_widgets.Agreement

::: squid_ui_widgets.AgreementParticipant

::: squid_ui_widgets.AgreementResolveHandler

::: squid_ui.tallies.TallyOption

## Ranked lists and rosters

::: squid_ui_widgets.RankedList

::: squid_ui_widgets.RankedEntry

::: squid_ui_widgets.RankedListState

::: squid_ui_widgets.SourceRankedList

::: squid_ui_widgets.place_roster

::: squid_ui_widgets.RosterEntry

::: squid_ui_widgets.RosterGroup

::: squid_ui_widgets.RosterSlot

::: squid_ui_widgets.RosterStatus

::: squid_ui_widgets.RosterPlacement

::: squid_ui_widgets.RosterOverflow

::: squid_ui_widgets.GridCell

## State machines and drivers

The machinery beneath the widgets, for building your own.

::: squid_ui_widgets.StateMachine

::: squid_ui_widgets.MachineControls

::: squid_ui_widgets.TransitionEvent

::: squid_ui_widgets.TransitionHandler

::: squid_ui_widgets.TransitionRoute

::: squid_ui_widgets.ComponentDriver

::: squid_ui_widgets.RouteDriver

::: squid_ui_widgets.RouteEncoder

## Advanced modules

| Module | Purpose |
|---|---|
| `squid_ui_widgets.guards` | The one guard whose refusal is a rendered question. |
| `squid_ui_widgets.testing` | Drive a machine's two shells with no frontend attached. |

# 30 — Structural patterns (Batch B)

## Goal

Add the structural batch: the interaction shapes applications currently hand-roll around
lists, settings, and search — master-detail browsing, immediate multi-choice commit,
domain-entity lookup, and a general section editor. The batch consumes plan [33](33-resources.md)'s
resource model for all async loading, plan [19](19-patterns.md)'s two-shell rule for pure
patterns, and plan [18](18-forms.md)'s form machinery for every edit surface. Nothing here
adds pagination or loading machinery: the survey behind this series validated
`SourceRankedList`'s seek support and the ≤5-buttons-else-select adaptation as already
correct, so `Browser` and `Lookup` are consumers of `WindowSource`/`WindowLoader`, not
rivals to them.

## 1. CommitPolicy

Two patterns in this batch decide *when* staged values become committed values. That
vocabulary must not fork, so it is one enum in `patterns/`:

```python
class CommitPolicy(StrEnum):
    EXPLICIT = "explicit"
    IMMEDIATE = "immediate"
```

`EXPLICIT` keeps the existing stage-then-Apply/Save shape. `IMMEDIATE` commits each valid
change in the same transition that staged it. The policy names when a pattern commits; it
never changes who merges or who owns persistence.

## 2. MultiChoicePanel immediate commit

`MultiChoicePanel` gains `commit: CommitPolicy = CommitPolicy.EXPLICIT`. Under
`IMMEDIATE`, `transition` runs the select/modal merge as today and then, when
`errors(new_state)` is empty, returns the state with `committed = staged` in the same
transition. `render` hides the Apply row and the summary line reads from `committed`.

An invalid staged set keeps the reader's gesture: staged stays uncommitted, the existing
`status` violation lines say why, and the next valid change commits everything. The
alternatives lose:

| On invalid change | Verdict |
|---|---|
| Keep staged, show errors, commit on next valid change | Chosen. The gesture survives and the cause is visible. |
| Revert staged to committed | Rejected. Destroys the gesture invisibly — a select echoes server state on the next render with no explanation. |
| Raise | Rejected. User input is never a bug. |

The component callback is named `on_commit` and fires only when `committed` changed; routing
immediate commit through that field is precisely what makes the same handler correct under
both policies. The pre-0.1 `on_apply` spelling is removed rather than aliased. Plan
[90](90-deferred.md)'s rejection of engine-side `Managed` merging stands: this changes when
the pattern commits, not who merges.

Consumers: `NotificationPanel` subscription kinds (`squid/bot/notifications_view.py`,
whose selection handler is immediate by nature today) and build tag/restriction editing.

## 3. Editor

`Editor` is a pure dual-shell pattern for named form and nested-pattern sections, an
optional live preview, and a commit policy. A form section owns one mapping. A nested
section adapts another pure `Pattern` through explicit load/dump callbacks, so Editor can
distinguish that pattern's interaction state (selection and pagination) from the value it
will commit (for example, a `CollectionEditor`'s ordered entries).

```python
class EditorSection[StateT, ValueT]:
    @classmethod
    def form(cls, key: str, label: TextLike, form: FormLike, ...) -> EditorSection[..., Mapping[str, object]]: ...

    @classmethod
    def pattern(
        cls,
        key: str,
        label: TextLike,
        pattern: Pattern[StateT],
        *,
        load: Callable[[ValueT], StateT],
        dump: Callable[[StateT], ValueT],
        summary: Callable[[ValueT], TextLike],
        issues: Callable[[StateT], Iterable[FormIssue]] | None = None,
    ) -> EditorSection[StateT, ValueT]: ...

    def value(self, state: EditorState) -> ValueT: ...

@dataclass(frozen=True, slots=True)
class EditorSectionState:
    key: str
    state: object
    committed: object

@dataclass(frozen=True, slots=True)
class EditorState:
    sections: tuple[EditorSectionState, ...]
    editing: str | None = None

type EditorValues = Mapping[str, object]
type EditorCommitHandler = Callable[
    [PatternEvent[EditorState], EditorValues, frozenset[str]], Awaitable[None]
]

class Editor:
    def __init__(
        self,
        title: TextLike,
        sections: Iterable[EditorSection[object, object]],
        *,
        key: str = "editor",
        preview: Callable[[EditorValues], ContentLike] | None = None,
        commit: CommitPolicy = CommitPolicy.EXPLICIT,
        commit_label: TextLike | None = None,
        validate: Callable[[EditorValues], Iterable[FormIssue]] | None = None,
    ) -> None: ...

    def component(
        self,
        *,
        initial: EditorValues | None = None,
        on_commit: EditorCommitHandler | None = None,
    ) -> ComponentShell[EditorState]: ...

    def values(self, state: EditorState) -> EditorValues: ...
    def form_for(self, state: EditorState, action: str) -> FormSpec | None: ...
```

Wizard's vocabulary stays inside Wizard: `WizardStep` and `WizardAnswer` are not reused by
Editor. `EditorSection` is individually generic in its interaction-state and value types;
the heterogeneous aggregate erases them to `object`, while `section.value(editor_state)`
recovers the precise `ValueT`. A `TypeVarTuple` does not help here because Python cannot map
each member of a variadic tuple into a keyed `EditorSection` wrapper. The sketch
`Editor(commit="Post")` conflated the policy with its label; the policy is `commit` and the
label is `commit_label`, defaulting to `chrome.save`.

Rendering: title; `preview(values)` placed as-is (degradation wrapping is the author's
choice, not the pattern's); one row per section with its label and summary. Form sections
open a prefilled form. Pattern sections open a namespaced child workspace with Back chrome.
Under `EXPLICIT` the commit row appears when dumped values differ from committed values and
validation passes. Under `IMMEDIATE` there is no commit row: a value-changing transition
commits every dirty section once the aggregate is valid. `on_commit` always receives the
complete committed mapping and the set of section keys changed by that commit.

`CollectionEditor` composes through `EditorSection.pattern`: `load` calls
`initial_from`, `dump` calls the new `values(state)` projection, and selection/paging do not
make Editor dirty. Under `IMMEDIATE`, dumped-value commits are natural `record()` sites for
plan [28](28-history.md) undo.

Proving consumer: `AccountPanel` profile editing, with a profile form plus a nested links
collection under `EXPLICIT`. `SettingsPanel`'s inline choices/toggles and the poll composer's
async option resolution are not forced through a form-section abstraction in this batch.

## 4. Browser

`Browser` is master-detail over an async collection: a windowed list, a selected entry, a
detail view with entry actions, and a way back. `Menu` is not this (it navigates a
predefined destination tree); `RankedList` is not this (its state is a position); semantic
`Items` already covers the static in-message case and remains the right tool there.
Browsing a *remote* collection needs an async fetch, which a pure pattern cannot perform,
so `Browser` is a real `Component` under plan 19's escape clause — the same justification
as `SourceRankedList`, and post-33 it follows that migration's shape:

```python
class Browser[ItemT](Component):
    opened: ItemT | None = state(None, persist=False)

    @resource(depends=(position,))
    async def window(self) -> LoadedWindow[ItemT]: ...

    def __init__(
        self,
        source: WindowSource[ItemT],
        *,
        key: str = "browser",
        identity: Callable[[ItemT], str],
        label: Callable[[ItemT], TextLike],
        detail: Callable[[ItemT], ContentLike | Component],
        summary: Callable[[ItemT], TextLike] | None = None,
        entry_actions: Callable[[ItemT], Sequence[Action | Link]] | None = None,
        overview: Callable[[LoadedWindow[ItemT]], ContentLike] | None = None,
        page_size: int = 10,
        on_open: Callable[[SelectionEvent], Awaitable[None]] | None = None,
    ) -> None: ...

    async def refresh(self) -> None: ...   # window.invalidate() around the visible anchor
```

Navigation writes `position`; the dependency turns the window `Pending(previous=...)`, so
the stale window stays visible during the fetch exactly per 33's contract, and render
matches `Pending | Ready | Failed` instead of hand-rolling a loading branch. Capability
chrome comes from `window_footer`; the pager binds `WindowLoader` operations, never a
hand-rolled cursor.

The overview renders `label(item)` (or `summary`) lines plus a select to open. The detail
view renders `chrome.back`, `detail(item)`, `entry_actions(item)`, and previous/next
within the loaded window. A `Component` returned by `detail` is embedded under
`f"detail-{identity(item)}"`, so per-entry keys never collide. It is created once per open
and retained beside the actual item in non-persistent state, preserving child state across
parent renders without pretending an identity alone can render an item that has left the
current window. `opened` stays internal with an `on_open` callback in v1.

Proving consumer: search results (`squid/bot/submission/search_view.py`). Its source adapter
retains per-window warning metadata for the `overview` hook. Claim review remains a good
future consumer; static account identities correctly remain static.

## 5. Lookup

`Lookup` is search-then-pick for domain entities — builds, accounts, tags — whose option
space exceeds a select. The boundary with the platform is explicit: Discord users, roles,
and channels inside a form already have `sl.discord.EntityField`, which is native and
better; `Lookup` exists for entities Discord has never heard of.

```python
class Lookup[ItemT](Component):
    query: str | None = state(None)
    picked: tuple[ItemT, ...] = state((), persist=False)

    @resource(depends=(query,))
    async def results(self) -> LoadedWindow[ItemT]: ...

    def __init__(
        self,
        search: Callable[[str], WindowSource[ItemT]],
        *,
        key: str = "lookup",
        identity: Callable[[ItemT], str],
        label: Callable[[ItemT], TextLike],
        description: Callable[[ItemT], TextLike] | None = None,
        minimum: int = 0,
        maximum: int = 1,
        picked: Sequence[ItemT] = (),
        on_pick: Callable[[ActionEvent, tuple[ItemT, ...]], Awaitable[None]],
    ) -> None: ...
```

A single-`TextField` search form writes `query` and resets paging; the dependencies invalidate `results`;
33's monotonic request tokens drop a stale completion when a second search lands during
the first fetch. That is worth stating plainly: the resource model deleted this pattern's
hardest bug before the pattern existed. Results page through `WindowLoader`; a select
picks; actual picked items are retained in runtime-only state and render as fields with
per-entry remove actions (or replace, when `maximum == 1`). The callback receives the
resolved item tuple rather than making the consumer repeat the lookup.

The source abstraction is the sharp decision:

| Shape | Verdict |
|---|---|
| `Callable[[str], WindowSource[ItemT]]` — a query resolves to a source | Chosen. `WindowLoader`, capabilities, and footer chrome compose unchanged. |
| A new `SearchSource` protocol with `search(query, position, extent)` | Rejected. Duplicates the Window/Position plumbing and every cursor affordance. |
| `Callable[[str], Awaitable[Sequence[ItemT]]]` | Not the contract, but supported via an adapter. |

The adapter ships as `sl.list_source(items)`, wrapping an immutable static sequence as an
exact offset-addressed `WindowSource` — small, and generally useful beyond `Lookup`.

No bot flow is migrated in this batch. Discord command autocomplete remains the better
interaction for existing command parameters; Lookup ships as a tested library boundary for
future mounted flows.

## Considered, not done

- **A pattern-shaped Browser.** A pure `state → tree` browser over a static tuple is
  nearly free once `Items` exists — and that is the point: `Items` already is that.
  The component earns its shell by owning a fetch.
- **`Ownership` on `Browser.opened` / staged Apply inside `Lookup`.** Both wait for a
  consumer; the callbacks are the v1 contract.
- **A second configuration vocabulary for `Editor`.** Sections are forms. Field types,
  validation, and prefill all come from plan 18; `Editor` adds orchestration only.

## Chrome

`Chrome` gains `apply`, `save`, `unsaved`, `search`, and `no_results`, all resolved by
`localize_chrome`.

## Landing order

`CommitPolicy` first, then MultiChoicePanel immediate commit (smallest consumer), then
`Editor` (uses `CommitPolicy` and `EditorSection`), then `Browser`, then `Lookup` (reuses
Browser's resource/window idioms).

## Verification

- `test_multichoice_pattern.py`: an `IMMEDIATE` valid select change commits in one
  transition and fires `on_apply` exactly once; an invalid change stays staged with errors
  shown and commits on the next valid change; the Apply row is absent; `EXPLICIT` behavior
  is unchanged.
- `test_editor_pattern.py`: form submits store values; the commit row is gated on dirty
  and on `validate`; `IMMEDIATE` reports all committed values and changed section keys;
  summaries round-trip through `format_prefill`; nested actions/forms keep routed parity;
  a `CollectionEditor` section composes while paging/selection remain non-dirty.
- `test_browser.py`: a position write turns the window `Pending(previous=...)` and the
  stale window stays visible; open/back; per-identity embed keys; the pager delegates to
  `WindowLoader` (a fake source records fetches); capability-gated footer; `strict_state`
  clean.
- `test_lookup.py`: a query write invalidates results; a stale completion is dropped;
  minimum/maximum picks; remove/replace; `no_results` chrome; `list_source` windows a
  static sequence exactly.
- `test_public_api.py`: every new export. Run focused tests with `--no-cov`, then
  `just typecheck` and `git diff --check`.

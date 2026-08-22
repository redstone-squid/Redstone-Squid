# 29 — Control vocabulary (Batch A)

## Goal

Add the first vocabulary batch that applications currently spell with raw Discord controls
or ad-hoc patterns: boolean toggles, modal multi-choice, portable upload values and visible
downloads, decisions, editable collections, and typed instants. The batch follows plan 10's
ownership names, plan 19's two-shell rule, and the existing semantic → primitive → scene
pipeline. `sl.resource` from [90](90-deferred.md) is an async loading descriptor; it is
unrelated to `PlanResult.resources["asset:…"]` and does not participate in this design.

## 1. Toggle

`Toggle` is a keyed, stateful semantic leaf. It is not `Choices` sugar: one boolean has one
identity. Encoding it as two choice keys leaks fake option identities into custom ids and
gives authors `ChoiceEvent.selected: tuple[str, ...]` where they mean `bool`.

```python
@dataclass(frozen=True, slots=True)
class ToggleEvent(ActionEvent):
    value: bool = False

type ToggleOwnership = Ownership[bool, ToggleEvent]
OFF: ToggleOwnership = Managed(initial=False)

@dataclass(frozen=True, slots=True)
class Toggle:
    key: str
    label: TextLike
    on: ToggleOwnership = OFF
    on_label: TextLike | None = None
    off_label: TextLike | None = None
    tone: Tone = Tone.NEUTRAL
    available: bool = True

def toggle(
    label: TextValue,
    *,
    key: str,
    on: ToggleOwnership = OFF,
    on_label: TextValue | None = None,
    off_label: TextValue | None = None,
    tone: Tone = Tone.NEUTRAL,
    available: bool = True,
) -> Toggle: ...
```

### Ownership conflict

The sketch `sl.toggle(value=sl.controlled(...))` conflicts with plan 10's rule that every
stateful node names its ownership field after the state it carries (`Choices.selection`,
`Details.open`, `Navigation.current`, `Items.opened`). The field is therefore `on`, mirroring
`Details.open`:

```python
sl.toggle(
    "Web notifications",
    key="web",
    on=sl.controlled(self.web_enabled, self._toggle_web),
)
```

`value=` is rejected because it breaks that rule.

Managed state gets a dedicated `PresentationSession.toggles: dict[str, bool]` and
`ToggleUpdate` in `SessionUpdates`, mechanically matching disclosure state. Reusing
`disclosures` is rejected: devtools and session dumps would call toggles disclosures and
make the vocabularies impossible to separate later.

Discord lowering emits one `Row((Button(...),))`. Its label is
`"{label}: {on_label or chrome.on}"` or `"{label}: {off_label or chrome.off}"`; tone maps to
button style. Managed presses write the toggle session store and invalidate. Controlled
presses call `on_change(ToggleEvent(value=not current))` and do not write session state.
There is one representation in v1. Collapsing a cluster into a multi-select was considered
and rejected: `MultiChoicePanel` already owns that interaction model. HTML receives the
existing `SceneButton`; `aria-pressed` or checkbox rendering is a renderer nicety, not a
new primitive. A checkbox primitive was considered and not added.

Touchpoints: `semantic.py`, `factories.py`, the keyed-leaf rewrite in
`runtime/component.py`, `planning/adaptation.py`, `runtime/presentation.py`, public exports,
and `Chrome.on`/`Chrome.off` plus localization. Consumers are notification web/DM controls
and account identity/page controls, all of which currently re-derive on/off labels.

## 2. MultiChoiceField

`MultiChoiceField` is a portable `ChoiceField` sibling. Every adapter can honestly render
multi-choice, so an `ExtensionField` form and capability gate are rejected.

```python
@dataclass(frozen=True, slots=True)
class MultiChoiceField[ValueT](FormField[tuple[ValueT, ...]]):
    options: tuple[ChoiceOption[ValueT], ...] = ()
    minimum: int = 0
    maximum: int | None = None
```

Option keys must be unique. `parse()` accepts one key or a submitted list/tuple, rejects
unknown keys and values outside `[minimum, maximum or len(options)]`, and returns values in
declaration order. Required empty input flows through the standard `_optional` error.
`format_prefill()` accepts typed values or keys and returns a tuple of keys. The short alias
is `MultiChoice`.

The value is `tuple[ValueT, ...]`, not a set, matching `Choices.selection` and making route
and display order deterministic. `ChoiceOption` is reused unchanged.

The Discord modal adapter emits `discord.ui.Select` with the declared min/max and a values
reader. A modal select must contain 1–25 options; other cardinalities raise
`LayoutInvariantError`. `MultiChoicePanel.form_for()` replaces its N `BoolField`s with one
field, lifting the small-panel modal ceiling from 5 to 25. Further consumers are submission
tag/restriction pickers and notification subscription kinds.

## 3. UploadedFile and Download

### Upload

Discord already has a working `FileField(ExtensionField[object])`. The missing contract is a
portable handler value:

```python
@dataclass(frozen=True, slots=True)
class UploadedFile:
    name: str
    media_type: str
    size: int
    url: str
    read: Callable[[], Awaitable[bytes]] = field(repr=False, compare=False)
```

The Discord reader wraps each `discord.Attachment` before `FormSpec.evaluate`; handlers
therefore never receive a Discord object. `FileField.parse()` returns
`tuple[UploadedFile, ...] | UploadedFile | None` according to its min/max contract. The field
stays Discord-scoped under `sl.discord`, with capability `forms.discord.file` and an optional
portable `TextField` URL fallback. Promotion to `forms.py` happens when a second frontend
implements upload. Native uploads support 0–10 files; larger maxima raise
`LayoutInvariantError`.

### Download

`Download` is the visible half of the asset pair and is a keyed semantic leaf:

```python
@dataclass(frozen=True, slots=True)
class Download:
    key: str
    label: TextLike | None
    asset: Asset
    description: TextLike | None = None
    emphasis: Emphasis = Emphasis.NORMAL

def download(
    label: TextValue | None,
    asset: Asset,
    *,
    key: str,
    description: TextValue | None = None,
    emphasis: Emphasis = Emphasis.NORMAL,
) -> Download: ...
```

The API sketch made `label` mandatory while also asking `Chrome.download` to supply a label
when absent. The implemented factory accepts `None` and resolves it to `chrome.download` at
planning time; the positional parameter remains explicit so asset-only controls do not
silently change call shape.

The node carries `Asset` inline. Referencing `Document.assets` by key is rejected because the
control and file declaration would be separated. `_download` hoists inline assets into the
adaptation context; the planner merges them with `Document.assets` into
`resources["asset:<key>"]`, deduplicating equal assets and raising `LayoutInvariantError` for
one key bound to different assets. `Document.assets` remains for invisible resources such as
`attachment://` images.

The full pipeline gains `primitives.File(asset_key, name, media_type)` and
`SceneFile(asset_key, name, media_type)`. A file costs one components-v2 item and no character
budget. Scene codec/schema carry the node. Discord renders `discord.ui.File` with an
`attachment://` URI and mount attaches the resource bytes. A URL-backed `StoredAsset`
degrades to a link button; non-URL stored references still raise.

The HTML renderer resolves a `SceneFile` against the `PlanResult`: inline bytes become a data
URI `<a download>`; a custom `asset_resolver` may return another URL; unresolvable files become
a visible disabled placeholder and are never silently omitted.

Consumers: schematic download/conversion, diagnostics/admin exports, and submission
schematic/media upload.

## 4. Decision and confirm

A decision is a pure pattern, not a semantic node: pending → decided, option disabling, and
finishing are lifecycle state. `Decision` being capitalized follows `Menu`, `Tabs`, `Wizard`,
and `MultiChoicePanel`; the lowercase API is the `confirm()` sugar.

```python
@dataclass(frozen=True, slots=True)
class DecisionOption:
    key: str
    label: TextLike
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL

@dataclass(frozen=True, slots=True)
class DecisionState:
    decided: str | None = None

type DecisionHandler = Callable[
    [PatternEvent[DecisionState], str], Awaitable[None]
]

class Decision:
    def __init__(
        self,
        prompt: ContentLike,
        options: Iterable[DecisionOption],
        *,
        key: str = "decision",
    ) -> None: ...

    def component(
        self,
        *,
        on_decide: DecisionHandler | None = None,
        finish_on: Collection[str] = (),
    ) -> ComponentShell[DecisionState]: ...

    def finish_actions(self) -> frozenset[str]: ...

def confirm(
    prompt: ContentLike,
    *,
    key: str = "confirm",
    on_confirm: Callable[[PatternEvent[DecisionState]], Awaitable[None]],
    on_cancel: Callable[[PatternEvent[DecisionState]], Awaitable[None]] | None = None,
    confirm_label: TextValue | None = None,
    cancel_label: TextValue | None = None,
    tone: Tone = Tone.DANGER,
) -> ComponentShell[DecisionState]: ...
```

`choose:<key>` sets `decided` only while it is `None`; later clicks are state no-ops, making
double-clicks safe. Rendering uses `controls.content(prompt)`, one action row, disabled
options after deciding, and `status(chrome.decided(label), tone=...)`. `finish_actions()`
returns every `choose:<key>` action. `confirm()` returns a ready `ComponentShell`; it is
pattern sugar, not a node factory. `DecisionState` is route serializable, so the router shell
works unchanged.

Decision is pre-commit consent; plan 28 history is post-commit regret. Irreversible actions
confirm. Reversible actions record. A destructive action uses exactly one of the two.

Consumers: settings reset, claim reassignment, poll cancellation, and consent flows.

## 5. CollectionEditor

`CollectionEditor` is a pure dual-shell pattern. Its entries use Wizard's route-serializable
form-value shape rather than arbitrary domain objects.

```python
@dataclass(frozen=True, slots=True)
class CollectionEntry:
    key: str
    values: tuple[tuple[str, object], ...]

@dataclass(frozen=True, slots=True)
class CollectionState:
    entries: tuple[CollectionEntry, ...] = ()
    selected: str | None = None
    page: int = 0

type CollectionChangeHandler = Callable[
    [PatternEvent[CollectionState], tuple[Mapping[str, object], ...]], Awaitable[None]
]
```

`CollectionEditor(title, *, key, create, edit=None, label, identity=None, minimum=0,
maximum=None, reorder=True, window_size=25)` defines the machine. Instance data belongs in
`initial_from()` or `component(initial=...)`, matching Menu/Wizard/MultiChoicePanel. Passing
items positionally to the constructor is rejected. `edit` defaults to the create form with
the selected entry as prefill.

Actions are visible-window selection, form-backed add/edit, remove, move up/down, and
`page:previous|next` using `patterns._paging.window()`. Limits gate add/remove; selection and
`reorder` gate edit/remove/movement. `errors()` reports limit violations and `form_for()`
provides routed-shell parity. Identity comes from the callback or stable minted ordinal keys.
Every mutation invokes `on_change` with the complete ordered tuple of mappings.

The editor's finite verbs are a private `StrEnum`, so its implementation does not scatter raw
strings. The shared `Pattern.transition(..., action: str)` channel remains open deliberately:
patterns define their own protocols, and parameterized routes such as `choose:<option-key>`,
`submit:<step-key>`, and `page:<group-key>:next` contain author data that no closed enum can
enumerate. Replacing that channel with a tagged route value would be a framework-wide routing
protocol change, not an enum cleanup local to this pattern.

Arbitrary `ItemT` payloads are rejected because they break router serializability. Staged
Apply is considered but not implemented: granular changes are the contract, and plan 30's
Editor owns commit policy so it can compose this pattern unchanged. Reordering is selected
entry move-up/move-down; drag and jump-to-position are deferred.

Consumers: poll options, settings role weights, and account profile links.

## 6. Timestamp, TimeField, and DateTimeField

Typed JSON requires a semantic node and primitive; lowering directly to text is insufficient.

```python
class TimeStyle(StrEnum):
    SHORT_TIME = "t"
    LONG_TIME = "T"
    SHORT_DATE = "d"
    LONG_DATE = "D"
    SHORT_DATETIME = "f"
    FULL = "F"
    RELATIVE = "R"

@dataclass(frozen=True, slots=True)
class Timestamp:
    instant: datetime
    style: TimeStyle = TimeStyle.SHORT_DATETIME
    label: TextLike | None = None
```

`timestamp()` rejects naive datetimes at the factory boundary. Adaptation lowers the node to
`primitives.Time(instant, style, prefix)`. The solver charges the raw Discord markup length.
`SceneTime` serializes the instant as UTC ISO-8601 plus style and optional prefix. Discord
renders `<t:unix:style>`; HTML renders `<time datetime="…" data-squid-style="…">…</time>`.
Relative HTML presentation is client work (or a title fallback), not server-side string
flattening.

`md(t"…")` interpolation additionally recognizes `Timestamp` and aware `datetime`: the
Discord Markdown dialect emits the Discord token and plain text emits ISO. This is
Discord-dialect sugar; the block node remains portable. Text-token-only is rejected because
JSON loses the instant. Primitive-only is sufficient for the core, but primitive plus
interpolation is the chosen overall API.

```python
@dataclass(frozen=True, slots=True)
class TimeField(FormField[time]):
    minimum: time | None = None
    maximum: time | None = None
    placeholder: TextLike | None = "HH:MM"

@dataclass(frozen=True, slots=True)
class DateTimeField(FormField[datetime]):
    timezone: tzinfo = UTC
    minimum: datetime | None = None
    maximum: datetime | None = None
    placeholder: TextLike | None = "YYYY-MM-DD HH:MM"
```

Parsing uses `time.fromisoformat` and `datetime.fromisoformat` (including a space separator),
with existing “Enter a …” errors and inclusive bounds. A naive submitted datetime receives
the field timezone; returned values are always aware. Prefill uses ISO format. Short aliases
are `Time` and `DateTime`. The timezone belongs on the field so the host can supply the
guild/user zone; a form never returns a naive datetime.

Consumers: poll deadlines and close-time display, claims/account timestamps, and diagnostics
uptime.

## Chrome

`Chrome` gains `on`, `off`, `download`, `confirm`, `cancel`, `decided(label)`, `add`, `edit`,
`remove`, `move_up`, and `move_down`, all resolved by `localize_chrome`.

## Verification

- `test_toggle.py`: factory, managed/controlled writes, invalidation, stale-session ownership,
  keyed rewrite, and lowering shape.
- `test_forms.py`: multi-choice ordering/cardinality/required/prefill/duplicates and temporal
  parsing, bounds, zoning, and prefill.
- `test_form_discord.py`: select shape and 25-option limit; upload wrapping and 10-file limit.
- `test_decision_pattern.py`: one-way decision, disabled controls, confirm handlers/tone, and
  finish events.
- `test_collection_editor.py`: add/edit/remove/limits/reorder/paging/change payloads and routed
  form parity.
- `test_assets_download.py`, `test_scene.py`, `test_mount.py`, and `test_html_renderer.py`:
  asset hoisting/deduplication, scene codec, Discord attachment/URL behavior, and HTML links or
  placeholders.
- `test_text.py` and `test_factories.py`: timestamp dialect interpolation and naive rejection.
- `test_public_api.py`: every export. Run focused tests with `--no-cov`, then `just typecheck`,
  `alembic heads`, and `git diff --check`. `ScaleField` remains deferred to plan 31.

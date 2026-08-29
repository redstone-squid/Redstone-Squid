# 71 — The target is a product type

## Why

`TargetProfile` is an eleven-field bag with `limits: object | None` and `dialect: object | None`
(`planning/target.py:39,41`), and the type parameters `[ModeT, AdapterT, BodyT]` that were meant to
make it precise are not tied to any of its data. The one fact the whole planner turns on — *which
Discord message shape am I compiling to* — is therefore encoded five times and reconciled by hand:

| Encoding | Where |
|---|---|
| `id` string (`"discord.components-v2"`) | `planning/discord.py:14-15` |
| `mode` marker type (`ComponentsV2Target`) | `target_types.py:11-16` |
| `limits` subclass (`V2Limits`) | `planning/limits.py:112,143` |
| `dialect` singleton (`V2_DIALECT`) | `planning/v2.py:442`, `planning/classic.py:479` |
| `DiscordMode` enum | `squid_ui_discord/presentation.py:29` |

Nothing keeps them in step, so use sites re-derive the axis from whichever encoding is nearest —
and they reach for the worst one. `mount.py:874-875` recovers the mode by `isinstance`-ing the
`object | None` field:

```python
self.limits = target.limits if isinstance(target.limits, DiscordLimits) else LIMITS
self.mode = DiscordMode.CLASSIC if isinstance(self.limits, ClassicLimits) else DiscordMode.COMPONENTS_V2
```

A target should be a **product of two axes** — a *dialect* (the Discord protocol mode: what a legal
message is) and an *adapter* (the library that realizes it: what has been verified to work) — with
everything else derived, the way a compiler names `x86_64-unknown-linux-gnu` rather than threading
arch, OS and ABI separately.

## What the erasure costs — four confirmed defects

1. **A crash.** `semantic_adaptation/regions.py:250,295` read `limits.total_components`, which
   exists only on `V2Limits`. Both functions are annotated `V2Limits`, but the planner feeds them
   `target.limits` narrowed only to `DiscordLimits` (`planner.py:506`), and they are reached from
   the paged-region path — any `sl.paged(...)`. `Paged` (`semantic.py:743`) is not mode-gated, and
   `ClassicLimits` is a `slots=True` frozen dataclass. **`sl.paged(...)` under the classic target
   raises `AttributeError`.** Pyrefly cannot see it because the value launders through
   `object | None`.
2. **A wrong-type argument that type-checks.** `actions.py:46,99` pass `self.mount.limits` (a
   `DiscordLimits`) into `build_modal(..., limits: V2Limits)` / `build_form_modal`
   (`modal.py:443,465`). It works only because modal caps happen to live on the base class — nothing
   says so, and nothing would catch it if that stopped being true.
3. **An unsatisfiable protocol.** `Renderer[OutputT]` (`squid_ui/renderer.py`) declares
   `draw(self, scene: SceneDocument, ...)`, while `V2Renderer.draw` narrows to
   `SceneDocument[SceneComponentsV2]` (`renderer.py:108-114`). Parameters are contravariant, so
   **neither Discord renderer can satisfy the protocol**; it is declared and structurally dead.
4. **Seven invented defaults.** `getattr(limits, "embed_fields", 25)` and friends
   (`semantic_adaptation/lowering.py:395`, `semantic_adaptation/regions.py:127`,
   `layout_measurement/realization.py:112,123,124,130,136,140`) — the shared measurer reaching
   classic-only caps through a `DiscordLimits` and silently substituting a number when absent.

Beyond those: protocol capabilities are recovered by set subtraction (`target.py:72`);
`TargetRegistry` keys on the protocol id alone (`targets.py:37`) so two adapters for one protocol
cannot coexist; `routing.py:41,277` validates custom-id length against the `V2Limits` module global
rather than anything mode-neutral; `Wire` is defined twice, identically (`renderer.py:51`,
`classic_renderer.py:54`); and ~35 functions in `conformance.py`, `inspection.py`, `testing.py` and
`modal.py` are annotated `V2Limits` while reading only mode-neutral caps, which is what makes them
unusable on the classic path.

The 2026-08-26 split of `adaptation.py` into `semantic_adaptation/` and of `measurement.py` into
`layout_measurement/` moved every one of these sites and fixed none of them. That is the argument
for treating this as a type problem rather than a layout problem.

**Blast radius is small.** The bot names no target and uses no durability: `V2_TARGET`,
`CLASSIC_TARGET` and `TargetProfile` appear in zero files under `squid/`. Everything below is
confined to the two packages and their tests, and there are no live durable snapshots to invalidate.

## Two things that are not wrong and must survive

- **The `ComponentsV2Target` / `ClassicTarget` marker hierarchy is load-bearing.** It types *nodes*,
  not targets: `packages/squid-ui-discord/tests/typing_targets.py` pins
  `Variants.of(v2_only, classic_only) -> Variants[ComponentsV2Target | ClassicTarget]` and that
  `plan(v2_only, target=classic())` is a type error. That file keeps passing unchanged but for
  renames.
- **`controls: 25` and `attachments: 10` are Discord limits, not discord.py limits.** 25 is
  5 rows x 5 buttons; 10 is Discord's per-message file cap. discord.py merely happens to be the only
  layer checking them locally. The docstrings at `limits.py:53-55` and `:155` say "library cap",
  which is misleading — correct the prose, leave the values. The adapter axis carries capabilities,
  extensions and a version gate, and **no limits at all**.

## The shape

```python
# squid_ui/planning/dialect.py — axis 1: the protocol
class TargetDialect[LimitsT: DiscordLimits, BodyT: SceneBody, ModeT](Protocol):
    id: str
    version: int
    capabilities: frozenset[Capability]   # protocol only; no adapter or extension strings
    mode: type[ModeT]
    body_type: type[BodyT]
    default_limits: LimitsT
    realizes_extensions: bool             # V2 True, classic False

    def normalize(self, nodes: Sequence[Node], target: Target[LimitsT, BodyT, ModeT, Any]) -> tuple[Node, ...]: ...
    def validate(self, nodes: Sequence[Node], limits: LimitsT) -> None: ...
    def paginate(self, nodes, *, key, capacities, limits: LimitsT, chrome, nav, broker) -> tuple[MeasuredLayout, int]: ...
    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> BodyT: ...


# squid_ui/planning/target.py — the product
@dataclass(frozen=True, slots=True)
class Target[LimitsT: DiscordLimits, BodyT: SceneBody, ModeT, AdapterT]:
    dialect: TargetDialect[LimitsT, BodyT, ModeT]
    adapter: AdapterProfile[AdapterT]
    limits: LimitsT                                       # effective, after any reservation
    selected_adapter_capabilities: frozenset[str] | None = None
```

`id`, `version`, `mode`, `body_type`, `protocol_capabilities`, `capabilities`, `extensions`,
`budgets` and `capacities` become **properties reading one of the two axes**. Six stored fields go.

Two payoffs worth naming:

- The dialect's four methods can be typed on `LimitsT` / `BodyT`. Today `V2Dialect.validate` narrows
  to `V2Limits` against a protocol declaring `DiscordLimits` (`dialect.py:146`, `v2.py:424`,
  `classic.py:455`) — a Liskov violation that passes only because the protocol is duck-typed. Bound
  to its own limits type, the narrowing becomes correct rather than tolerated.
- `restrict_adapter_capabilities` stops computing
  `protocol = self.capabilities - self.adapter_capabilities`. Protocol capabilities live on the
  dialect and are never mixed in, so there is nothing to subtract back.

## 0. Fix the classic `sl.paged` crash

Independently landable, no API change, backportable. First, so the fix is not entangled with the
refactor.

- Add to `DiscordLimits` (`planning/limits.py:43`) an abstract `component_budget: int` beside the
  existing `fits_controls`: "the most components one page of this mode may spend."
  `V2Limits` answers `total_components` (40); `ClassicLimits` answers `controls` (25).
- Replace both reads at `semantic_adaptation/regions.py:250,295`.
- Regression test in `packages/squid-ui-discord/tests/test_classic_target.py`: plan a document
  containing `sl.paged(...)` against the classic target and assert a `SceneClassicMessage` rather
  than a raise. It must fail before the change.

## 1. Limits become three honest types

The root of defects 2 and 4 is that there was no name for "the caps every Discord component obeys",
so every function reading them borrowed `V2Limits`. Give it a name.

```python
@dataclass(frozen=True, slots=True)
class ComponentLimits:
    """Caps every Discord component obeys, whichever message mode holds it."""
    row_buttons=5; button_label=80; link_url=512
    select_options=25; select_placeholder=150
    option_label=100; option_value=100; option_description=100
    modal_title=45; modal_components=5; modal_text=4000
    label_text=45; label_description=100
    text_input_placeholder=100; text_input_value=4000
    custom_id=100

@dataclass(frozen=True, slots=True)
class EmbedLimits:
    title=256; description=4096; fields=25
    field_name=256; field_value=1024; footer=2048; author=256

@dataclass(frozen=True, slots=True)
class DiscordLimits:                       # abstract; message-wide budgets live on subclasses
    components: ComponentLimits = COMPONENT_LIMITS
    attachments: int = 10
    embeds: EmbedLimits | None = None      # None on V2, populated on classic
```

- **`embeds: EmbedLimits | None` deletes all seven invented defaults.** An optional capability gets
  an optional value, so the type checker forces the guard at each read in
  `planning/classic.py:121-233` and `layout_measurement/realization.py:112-140` instead of a
  `getattr` silently substituting 25.
- **Sort the ~35 over-narrowed annotations by what they actually read**, which is the point of
  having three types:
  - `ComponentLimits` —
    `conformance._report_custom_id/_conform_button/_conform_select/_conform_text_input/conform_modal`,
    `inspection._audit_button/_audit_select/_audit_row`, `testing.modal_problems`,
    `modal.build_modal/build_form_modal`. This is what fixes defect 2: `actions.py:46,99` pass
    `mount.limits.components` and it checks.
  - `V2Limits` — genuinely V2: `conformance._conform_gallery/_conform_text_budget`,
    `inspection.audit/_audit_gallery/_audit_media/_audit_section`, `testing.payload_problems`,
    `fragments._preflight` (they read `total_text`, `total_components`, `gallery_*`,
    `section_texts`).
  - `DiscordLimits` — the shared planning layers: `semantic_adaptation/model.py:31`
    (`_Context.limits`), `decisions.py:66,193,210,240,288,325`, `lowering.py:222`,
    `regions.py:125,244,278`, and `frontier.py:194,204`. **Rule: a shared layer may only touch what
    `DiscordLimits` declares** — anything mode-specific goes through a declared member
    (`fits_controls`, `component_budget`, `text_axes`, `capacities`, `components`, `embeds`) or
    moves onto the dialect.
- **`routing.py:41,277` reads `COMPONENT_LIMITS.custom_id`.** Custom-ID length is identical in both
  modes, so once "mode-neutral cap" is an instantiable value the complaint that routing bypasses the
  target dissolves rather than needing the target threaded into route construction.
- **Delete the name-to-attribute-name indirection.** `budgets: Mapping[str, str]` exists only so
  `Target` can `getattr(limits, attribute)` in three places (`target.py:110,117,142`) and
  `layout_measurement/solver.py:382` in a fourth. Replace with two declared members on
  `DiscordLimits`: `capacities -> Mapping[Axis, int]` and
  `with_capacities(reductions: Mapping[Axis, int]) -> Self`. `Target.reserve` calls the latter; no
  string-keyed `getattr` survives anywhere.
- **`_limit_values`' `__dataclass_fields__` duck-typing (`target.py:22-29`)** becomes a declared
  `DiscordLimits.digest()`, since the value is no longer `object`.
- **Axes become an enum.** The bare strings in `limits.py:22-40` (`DISPLAY_TEXT`, `CONTENT_TEXT`,
  `EMBED_TEXT`, `COMPONENTS`, `ATTACHMENTS`, `EMBEDS`, `ROWS`, `CONTROLS`) become `Axis(StrEnum)`,
  matching `Capability`, and `ResourceCost.values` becomes `Mapping[Axis, int]`.

## 2. The dialect becomes the protocol axis

- `TargetDialect` gains the seven data members above and its three type parameters. Its module
  docstring's rule ("a fifth method is the signal to go extract something") is about *methods*; say
  so in one clause so the added data is not read as a licence.
- `V2Dialect` (`v2.py:416`) and `ClassicDialect` (`classic.py:447`) declare their id, version,
  capabilities, mode, body type, default limits and extension support. `V2_TARGET_ID`,
  `CLASSIC_TARGET_ID`, `V2_PROTOCOL_CAPABILITIES` and `CLASSIC_PROTOCOL_CAPABILITIES`
  (`planning/discord.py:14-45`) move onto the dialects and stop being restated by the two factories.
- `normalize` drops its `limits` parameter: it already receives the target that owns them
  (`dialect.py:141`, called from the search).
- `realizes_extensions` replaces a silent divergence: today `components_v2_target` passes
  `adapter.extensions` and `classic_target` simply omits the argument (`discord.py:57-87`), so
  classic's inability to draw a native item is expressed only by a missing keyword.

## 3. `Target` is the product

- Rename `TargetProfile` to `Target` in `squid_ui.planning`; it becomes the only such class.
- Fields collapse to the four above; the rest become derived properties.
- Delete `squid_ui_discord.Target` (`squid-ui-discord/src/squid_ui_discord/target.py:31-88`) — its only job
  was two classmethods plus `_from`, an eleven-field manual copy that silently drops any new base
  field. The conveniences become module-level `v2()` / `classic()` constructors in `squid_ui_discord`,
  keeping their `@overload` pairs so the adapter-less call still infers `DiscordPy27Adapter`.
- Delete, with reasons:
  - `TargetProfile.resources` (`target.py:47`) — never populated; `budgets` always falls through to
    the limits, which `limits.py:81-88` already documents as the single declaration.
  - `TargetRequirements` (`target_types.py:38-42`) — exported, never consumed.
  - `_dialect_for`'s V2 default (`planner.py:89`) and the limits `isinstance` fallback
    (`planner.py:506`) — a target now always has both.
  - `_v2_limits` / `_classic_limits` (`composition.py:41`, `classic.py:55`),
    `inspection._v2/_classic` (`inspection.py:212-217`), and both casts at `mount.py:1386,1391`.
  - The dead `limits` parameter on `inspection.cost` (`inspection.py:170`, which does `del limits`).
- `_Search` (`planner.py:175`) stops carrying `target`, `dialect` and `limits` as three fields.
- `planner.py:721` hashes both `target.fingerprint` and the limits into the plan cache key; the
  fingerprint already covers the limit values, so drop the duplicate.

## 4. Identity is the triple

- `Target.triple -> str`, `f"{dialect.id}+{adapter.name}"`, e.g.
  `"discord.components-v2+discord.py"`. It names both axes, so two adapters for one protocol become
  distinguishable.
- `TargetRegistry` keys on `triple`, removing the collision its own docstring apologises for.
- `fingerprint` (`target.py:91-103`) covers dialect id + version + protocol capabilities + adapter
  name + selected adapter capabilities + limits digest. The dialect object and extension adapters
  stay excluded for the documented reason: they are process-local.
- `MountState` (`durability/__init__.py:100-115`) records `target_triple` in place of `target_id`.
  **This invalidates stored snapshots** — free here, but state it in the migration note.
- **`SceneDocument.target` stays the dialect id, not the triple** (`scene/model.py:388`). A planned
  scene is adapter-independent: any renderer for that protocol may draw it. Only the durable *mount*
  snapshot needs the triple, because only it must rebuild against the same budgets.
- Public constants become `DISCORD_V2_DPY27` and `DISCORD_V1_DPY27`, naming both axes at the call
  site. `V2_TARGET` / `CLASSIC_TARGET` are removed rather than aliased — two names for one value is
  what this plan exists to delete. `MountDefaults.target` (`defaults.py:56`) and the test references
  update mechanically.

## 5. The renderer protocol becomes satisfiable

- `Renderer[BodyT: SceneBody, OutputT]` with
  `draw(self, scene: SceneDocument[BodyT], *, plan: PlanResult[BodyT] | None = None) -> OutputT`.
  That fixes defect 3: the contravariance failure was the unparameterized `SceneDocument`. Both
  Discord renderers already default `wire=None`, so an extra defaulted keyword-only parameter leaves
  them structurally compatible — no protocol change is needed to accommodate it.
- Pin it: a test asserting `V2Renderer`, `ClassicRenderer` and `html.Renderer` each satisfy
  `Renderer[...]` at the type level. A declared protocol nothing implements is how defect 3 survived.
- **Dedupe `Wire`.** The identical
  `type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]` at `renderer.py:51` and
  `classic_renderer.py:54` collapses into one definition. It stays in `squid_ui_discord` — it returns a
  discord.py object and cannot move into the portable package.
- **Drop `html/renderer.py:87`'s `scene.target != V2_TARGET_ID` check.** The body-type check two
  lines below (`isinstance(scene.body, SceneComponentsV2)`) is the real gate, and it is both
  stricter and honest: what the previewer can draw is a component tree, not a target id. Removing it
  also removes the portable HTML renderer's import of a Discord target constant.
- **Rename `TextDialect` to `Markup`** (`text.py:11`) and `SceneText.dialect` to `SceneText.markup`.
  One word currently means both "markup language" and "protocol mode", which plan 67's naming rules
  forbid, and that collision is part of what prompted this plan. **The wire format does not change**:
  `codec.py:291,452` and `schema.py:219` map the JSON key `"dialect"` explicitly, so the key stays
  and only the Python names move.

## 6. Extensions stop being `object`

- `PreparedExtension[ResourceT]` and `ExtensionAdapter[ResourceT]` (`planning/adapter.py:55-67`), so
  each adapter declares what it produces and the renderer's downcast has something to check against.
  `AdapterProfile.extensions` stays `Mapping[str, ExtensionAdapter[Any]]` — the mapping is genuinely
  heterogeneous, keyed by extension kind, and `Any` at the container is the honest answer rather
  than a fiction. Say so in the docstring instead of leaving a reader to wonder.
- A `JsonValue` alias replaces `Mapping[str, object]` for `PreparedExtension.scene_payload`
  (`adapter.py:60`) and `SceneExtension.payload` (`scene/model.py:213`), so "JSON-safe" is stated by
  the type rather than only by prose.
- The synthesized `f"extension.{kind}"` capability string is spelled in four places
  (`adapter.py:98`, `target.py:65,74`, `discord.py:55`); give it one helper.
- Capability namespaces get typed apart: `dialect.capabilities` is `frozenset[Capability]`, and the
  five `ADAPTER_*` constants (`adapter.py:70-74`) become an `AdapterCapability` StrEnum.
  `Target.capabilities` stays the `frozenset[str]` union, so the ~10 membership sites are untouched.

## 7. The adapter binds the dialect

`mount.py:1374-1393` branches on mode four times, and its own docstring already names the product
this plan builds: *"The target decides the dialect, the renderer, the view factory, and the message
mode — and nothing else."* Replace the four branches with one `DiscordBinding` value keyed on
`dialect.id`, holding `(composer, renderer_factory, view_factory, DiscordMode)`. `DiscordMode` keeps
its independent job — describing an *observed* message through `mode_of(message)` reading
`message.flags.components_v2` (`presentation.py:45`) — and stops being inferred from a limits
subclass.

## Scope boundary

`squid/` has its own `MediaLimits`, `SchematicLimits` and `SearchTarget`. Those are application
types that merely share words with this vocabulary; they are untouched and unrelated.

## Verification

1. **Baseline first.** These packages have known pre-existing failures and a hang in `test_lookup`,
   so record a pre-change run before attributing anything:
   `uv run pytest packages/squid-ui/tests packages/squid-ui-discord/tests --no-cov -q`.
   Same for `just typecheck` — the tree is not at zero Pyrefly errors, so diff against a pre-change
   run and read only the files you touched.
2. **0 proves itself**: the new `sl.paged` + classic test fails before and passes after.
3. **Defect 2 becomes visible**: after 1, temporarily reverting `actions.py:46` to pass
   `mount.limits` must be a Pyrefly error. If it is not, the split did not do its job.
4. **Defect 3 becomes checked**: the new renderer-satisfies-protocol pin fails against today's
   `Renderer[OutputT]` and passes after 5.
5. **Type-level contract**: `packages/squid-ui-discord/tests/typing_targets.py` still pins the same
   variance and the same two `pyrefly: ignore` expectations after renaming. If an ignore becomes
   unnecessary, the node-typing guarantee has been weakened, not improved.
6. **Limits are still Discord's**: `test_limits_crosscheck.py` and `test_limits.py` unchanged in
   values. The `ComponentLimits` / `EmbedLimits` extraction edits paths, never numbers.
7. **Wire format unchanged**: `test_scene.py` and the JSON Schema round-trip pass untouched — 5's
   `Markup` rename and 4's triple both stop short of the wire. Assert the emitted key is still
   `"dialect"`.
8. **Durability**: `test_target_recovery.py` extended for triple-keyed resolution, including that a
   snapshot naming a registered protocol but an unregistered adapter is refused by name rather than
   resolving to the wrong profile — the collision 4 closes.
9. **Architecture tests**: `tests/architecture/test_discord_components_v2.py` (its
   `CLASSIC_TARGET_HOMES` five-file allowlist) and `tests/architecture/test_naming.py`.
10. **End to end**: the bot names no target, so it exercises the default V2 path only. Launch it and
    open one mounted panel (`/settings`) plus one static render to confirm that path is untouched.
11. **Grep gates** — each returns zero when the plan is complete:
    `getattr(limits`, `getattr(context.limits`, `getattr(self.limits`, `isinstance(.*Limits)`,
    `cast(V2Limits`, `cast(ClassicLimits`, `: object | None`.

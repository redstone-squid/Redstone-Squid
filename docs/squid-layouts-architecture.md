# squid-layouts architecture and API interactions

squid-layouts separates UI intent, target planning, and drawing. Discord is one frontend,
not the data model: the same resolved scene can be drawn as Discord Components V2 or safe
HTML, serialized to JSON, and handed to another process.

## End-to-end flow

    Component state
        |
        | render()
        v
    authoring IR -- Embed expansion and key namespaces
        |
        v
    plan(target) -- capabilities, limits, degradation, pagination
        |
        +-- PlanReport (notes and fingerprints)
        +-- ephemeral ActionBindings (never serialized)
        v
    immutable SceneDocument -- SceneCodec JSON and JSON Schema
        |
        +-- DiscordRenderer --> discord.ui.LayoutView
        +-- HtmlRenderer ----> safe semantic HTML

The planner is the only layer allowed to choose an alternate, drop content, split a page, or
spend a target resource. A renderer is mechanical. If Discord drawing needs to clamp after
planning, that is a DrawInvariantError, not a second degradation mechanism.

## Which entry point to use

| Need | API | Result |
|---|---|---|
| Stateful Discord interaction | Mount(component) | lifecycle, events, paging, edits |
| Static Discord message | render_static(document) | discord.ui.LayoutView |
| Discord view plus diagnostics | compose(document) | Composition |
| Portable planning | plan(document, target=...) | PlanResult |
| Browser or preview drawing | HtmlRenderer.draw(scene) | HTML string |
| Cross-process transport | SceneCodec.dumps and loads | canonical protocol JSON |
| Resume an opted-in session | ComponentRegistry and MountManager | restored Mount |

compose is the Discord convenience path: plan for DiscordV2Target, draw with
DiscordRenderer, then strictly audit the result. Detached composition can pass
reserved_text; composing the complete document is preferable because the planner can see
every cost.

## Authoring IR and structural adaptation

Exact nodes such as Row, Gallery, and Section describe a shape that must already obey local
target limits. Semantic nodes such as ActionGroup, MediaCollection, and Choice let target
lowering choose or chunk a representation.

Text overflow is a per-node policy:

- Truncate and Spill shorten content.
- Alt and Alts provide semantic text ladders and per-entry drop priority.
- Paginate with an explicit key preserves content across independent pages.
- Fold returns components under structural pressure.
- Drop and Never make omission or non-degradation explicit.

Choice is capability selection; Fold is resource-pressure degradation. Capability branches
lower first, then the solver greedily folds the lowest-priority available alternate until
the target fits.

Target-native features use Extension(kind, version, payload, fallback). A target adapter
prepares and measures the native resource once. Unsupported targets use the mandatory
portable fallback. Extension payloads in scenes are versioned and JSON-safe.

## Components and Vue-inspired reactivity

Components render synchronously from state. state observes assignment and nested list, dict,
and set mutation. Factories avoid shared mutable defaults:

    class Search(sl.Component):
        query: str = sl.state("")
        results: list[str] = sl.state(factory=list)

        @sl.computed
        def title(self) -> str:
            return f"{len(self.results)} results for {self.query}"

computed caches until the component tree invalidates. batch coalesces related writes.
transaction also restores every touched field if an exception escapes. Mount dispatch wraps
mutating actions in a transaction, so a failed callback cannot leave state half-applied.

state(persist=False) marks runtime-only data that durable snapshots omit. Persistent state
must be JSON-safe.

Children appear through explicit keyed boundaries:

    def render(self):
        return sl.Panel((
            self.embed(self.filters, key="filters"),
            self.embed(self.results, key="results"),
        ))

Expansion scopes both action keys and pager keys, detects cycles and duplicate instances, and
gives Mount deterministic on_mount and on_unmount ownership. A Fold branch accepts only a
child that expands to one node; a Panel may flatten a multi-node child.

## Actions and frontend adapters

Components receive PressEvent or SelectionEvent, not discord.Interaction. Events expose
portable actor facts and response intents: notice, present_form, download, redirect, and
finish. Each frontend implements ActionResponder; Discord details live in
DiscordActionResponder.

| Policy | Concurrency | Stale control | State writes |
|---|---|---|---|
| EXCLUSIVE | serialized per mount | ignored and acknowledged | transactional |
| REBASE | serialized per mount | resolves newest binding | transactional |
| PARALLEL_READ | may overlap | allowed | rejected and rolled back |
| IMMEDIATE | may overlap | allowed | transactional; author accepts races |

Use EXCLUSIVE for ordinary mutations, REBASE when the same logical action should apply to
newest state after waiting, PARALLEL_READ for side-effect-free reads, and IMMEDIATE only when
concurrency is deliberately handled elsewhere.

## Pagination

Every planned Paginate needs an explicit unique key. Mount stores a cursor per key; embedded
components prefix it automatically. The solver measures all active footers and navigation IR
to a fixed point, so controls spend real component budget.

A paginator scene record contains a content fingerprint. When content under one key changes,
Mount resets only that cursor. per=N is count-based pagination; the default fills by target
text budget. Both use the same NavFactory.

## Scenes and renderers

SceneDocument is immutable and contains no callbacks or native frontend objects.
PlanResult.bindings and PlanResult.resources are ephemeral side tables for a live frontend.

SceneCodec provides canonical JSON, fingerprints, and a Draft 2020-12 schema through schema
and schema_json. Protocol 0 is experimental; incompatible changes increment the protocol.

HtmlRenderer emits escaped semantic markup, action identifiers, policies, and pager metadata.
Standalone mode includes Discord-like CSS. It preserves planned structure; pixel-level
fidelity also needs the website's chosen Discord-markdown and emoji renderer.

## Durable mounts

Durability is opt-in:

1. Register a stable root key, positive version, and host factory in ComponentRegistry.
2. Build and attach the mount to MountManager.
3. Checkpoint at a host-chosen durability boundary.
4. Restore through the registry and a SnapshotStore implementation.

Snapshots contain declared state by keyed component path plus page cursors. They never contain
callbacks, native items, service objects, or dynamic import instructions. The factory injects
dependencies. Version or tree-shape mismatches fail with SnapshotError and require an
explicit host migration.

MountManager starts no tasks. Database or Redis storage, checkpoint cadence, expiry, and
distributed ownership remain host policy.

## Deliberate boundaries and current gaps

- Modal submission still uses the Discord modal adapter. A portable form protocol is future
  work.
- Select overflow is an exact planning error. A semantic option-paging component belongs
  above SelectMenu.
- HTML action transport is not prescribed. Markup exposes action IDs; HTTP or WebSocket
  routing and authentication belong to the host.
- The distribution still depends on discord.py because the Discord adapter ships beside the
  portable core.

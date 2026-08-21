# squid-layouts: remaining migration

The framework (packages/squid-layouts) and its pilots landed 2026-08-20. Every semantic
layout (`error_layout`, `info_layout`, …) already renders through the engine via
`squid/bot/ui.py`; the pilots are `PagedList` (version list, pending queue, record gaps),
`ErrorReportBrowser`, and the build card (`build_handler.render_container`).

As of 2026-08-21, every listed consumer has a production `sl.Component` wired up
(`SearchResultsView`, `SettingsPanel`, `ClaimReviewComponent`), but several old classes
were never deleted after their replacements landed — they're dead weight kept alive only
by their own tests. In rough order of value:

- ~~**`SettingsPanelView`**~~ — **done** (redesign plan 09). The class, its six `discord.ui`
  controls and the `_compat_layout()` fallback are gone. Deleting them exposed a live defect:
  `RoleWeightModal` and `VoteEmojiModal` redrew the panel through `_compat_layout()`, so
  saving a role multiplier or emoji preset replaced the semantic message with the legacy
  card. Both now flush through the mount.
- **`ClaimReviewView`** (`squid/bot/claims_view.py`) — `ClaimReviewComponent` is what
  `squid/bot/verify.py` mounts; the old `ListPaginator` subclass is dead, referenced only
  by `tests/unit/bot/test_claim_review.py`. Deleting it is most of the way to retiring
  `squid/bot/utils/pagination.py`.
- **`BaseNavigableView` consumers** (`BuildInfoView`, `squid/bot/submission/ui/views.py`) —
  never migrated; zero real callers today (only `tests/unit/.../test_build_info_view.py`
  and a stale comment). Replace with `squid_layouts.Navigator`, then delete
  `squid/bot/submission/navigation_view.py` and its `__init_subclass__` wrapping.
- **`ExpiringLayoutView`/`ErrorHandledLayoutView` consumers** — `notifications_view.py` and
  `account_view.py` are **done** (redesign plan 09): `NotificationPanelView`,
  `AccountPanelView` and their controls are deleted. That took their tests with them, so
  `NotificationPanel` and `AccountPanel` now have no unit coverage of their own — worth
  writing back against the components. `consent.py` and the submission form/edit views
  already have superseded components as their real production path
  (`SubmissionFormComponent`, `BuildEditComponent`, etc. via `submit.py`/`edit.py`); the old
  view classes just weren't removed. `poll_wizard.py` and `errors.py` still need their
  component replacements. When the last consumer goes, the view halves of
  `squid/bot/errors.py` go too.
- **Delivery call sites** — `ui.reply(ctx, view, visibility=...)` exists; migrate
  `reply_layout`/`deliver_privately`/ad-hoc `ctx.send` sites organically.
- **`squid/bot/utils/components.py`** — delete when `card_container`,
  `truncate_display_text` (starboard/render.py, voting/rendering.py, build_handler sponsor
  caps), `StaticLayout`, and the edit/reply mechanics reach zero consumers; update the
  V2-guard carve-out list in `tests/architecture/test_discord_components_v2.py` at the same
  time (deliver.py already carries the framework-side carve-out).
- **`squid/bot/utils/pagination.py`** — delete once `ClaimReviewView` above is gone;
  `ListPaginator` has no other consumers.

Core design debts closed before further migration:

- The package root is semantic-first; exact Discord-shaped nodes live under `primitives`.
  `squid_layouts.primitives.presets` (`card`, `banner`, `listing`, `report`) — the
  pre-semantic-layer card builders — is deleted; all consumers migrated to semantic
  `Section`/`Fields`/`Media`/`Status`.
- Planning and drawing are separate; scenes are serializable and Discord plus HTML are
  independent renderers.
- compose is the only Discord plan/draw path. render_item now uses render_static and threads
  reserved_text, so detached build and vote cards no longer bypass solving or audit.
- Pagination controls and footers are measured IR, with independently keyed cursors and
  content-based reset. Count-paged lists share the same controls.
- Explicit Embed boundaries namespace actions and pagers; Navigator is an ordinary
  composition consumer.
- Structural Fold choices make component-count overflow solvable; entry priorities make
  text spill order semantic.
- Semantic Actions, Choices, Items, and Navigation adapt through legal keyed picker windows;
  36 actions become 25+11 without losing callbacks or merging declared groups.
- Sticky strategies are versioned per adapter and ranked by coarse lexicographic tiers.
  Bounded-search exhaustion is reported as `planner.search_fallback`, never degradation.
- Runtime-local resolved-plan caching rebinds current callbacks and meets the cold/cached
  planning-and-drawing latency acceptance budgets.
- A keyed root Document may promote structural overflow to whole-message pages. Local pagers
  take precedence and are never displayed simultaneously with root navigation.
- Durable records carry frontend-neutral message locators and expiry. Optional leased stores
  support exclusive startup recovery, renewal, and release without putting operational tasks
  inside the layout package.

Deliberate boundaries (documented in the package):

- Exact SelectMenu option overflow is a planning error; semantic interactions own option
  paging. Cross-page multi-select requires an explicit grouping or commit model.
- `PagedList` pages by count (UX pin); budget-fill paging is available via `Lines`+`Paginate`.
- `compose(into=view)` does not exist. A renderer owns a new output view so arbitrary
  pre-existing `discord.py` objects cannot bypass measurement.

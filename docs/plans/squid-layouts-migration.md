# squid-layouts: remaining migration

The framework (packages/squid-layouts) and its pilots landed 2026-08-20. Every semantic
layout (`error_layout`, `info_layout`, …) already renders through the engine via
`squid/bot/ui.py`; the pilots are `PagedList` (version list, pending queue, record gaps),
`ErrorReportBrowser`, and the build card (`build_handler.render_container`).

Still on the old machinery, in rough order of value:

- **`SearchResultsView`** (`squid/bot/submission/search_view.py`) — the cursor-driven
  paginator. As a component: callbacks fetch the next page, then assign state. Its select
  and detail text still hand-slice.
- **`SettingsPanelView`** (`squid/bot/settings_view.py`) — last big `card_container`
  consumer besides ClaimReviewView.
- **`ClaimReviewView`** (`squid/bot/claims_view.py`) — subclasses `ListPaginator`'s
  render/go_to contract; rewriting it as a component retires
  `squid/bot/utils/pagination.py` entirely.
- **`BaseNavigableView` consumers** (`BuildInfoView`) — replace with `squid_layouts.Navigator`,
  then delete `squid/bot/submission/navigation_view.py` and its `__init_subclass__` wrapping.
- **`ExpiringLayoutView`/`ErrorHandledLayoutView` consumers** — each view that becomes a
  component sheds them; when the last one goes, the view halves of `squid/bot/errors.py` go too.
- **Delivery call sites** — `ui.reply(ctx, view, visibility=...)` exists; migrate
  `reply_layout`/`deliver_privately`/ad-hoc `ctx.send` sites organically.
- **`squid/bot/utils/components.py`** — delete when `card_container`,
  `truncate_display_text` (starboard/render.py, voting/rendering.py, build_handler sponsor
  caps), `StaticLayout`, and the edit/reply mechanics reach zero consumers; update the
  V2-guard carve-out list in `tests/architecture/test_discord_components_v2.py` at the same
  time (deliver.py already carries the framework-side carve-out).

Core design debts closed before further migration:

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

Remaining design decisions (documented in the package):

- Exact SelectMenu option overflow is a planning error; a semantic option-paging component
  is future work.
- `PagedList` pages by count (UX pin); budget-fill paging is available via `Lines`+`Paginate`.

# Full pagination redesign

Redstone Squid paginates eight list endpoints, a Discord search view, and the web catalogue through two
near-duplicate HMAC-signed cursor implementations. The cursors are forward-only, silently return an empty page when
the anchor document churns out of the index, carry no total, and hide a facet-join bug that corrupts OFFSET math on
the sorted search path. This plan replaces the whole mechanism with transparent, user-typeable pagination
parameters.

No schema or Alembic changes are needed anywhere in this plan; it is query- and contract-level only.

## Locked decisions

These were settled with the user before design and are not open questions:

- **Delete signing entirely.** `SignedCursor`, `CursorCodec`, `CursorPosition`, and the `cursor_secret` config all
  go. Pagination parameters become plain documented values (`?offset=40`, `?after_id=123`). Signing never actually
  prevented enumeration.
- **Backward paging everywhere**, not just search — `/v1/builds`, `/v1/records`, `/v1/users/me/builds`, and the
  notifications inbox all gain it through one shared bidirectional keyset helper.
- **Totals everywhere**, enabling "Page N of M" in Discord and numbered pagination on the web.
- **Uncap the sorted path** (it is an indexed scan); keep the ranked cap but make it honest, so the end of results is
  explicit rather than a silent empty page.
- **Catalogue endpoints gain `sort`** with a small per-endpoint allowlist.
- **Envelope is `{items, total, next, prev}`** where `next`/`prev` are the plain parameter values to send back, not
  opaque strings.
- **Application services assemble pages, transports only map them.** The old signed-cursor helpers lived in the
  routes, which left catalogue paging assembled at the API layer while search paging was already assembled in
  `SearchService`. `squid/core/pagination.py` now holds the transport-neutral `Page`, `PageAnchor`, `PageSelector`
  and the two assemblers; `BuildQueryService.list_page`, `RecordService.list_page` and `NotificationService.inbox`
  return a `Page[...]`, and `squid/api/pagination.py` keeps only what is HTTP: the query parameters, the 400s they
  can earn, and the pydantic envelope. The exception is `/v1/tags`, `/v1/versions` and the build-schematics listing,
  whose services materialize a full in-memory list and have no pagination concept; those routes call the same
  `offset_page` assembler on the list the service returned.

## Global design

### Offset clamp

`MAX_PAGE_OFFSET = 10_000`, living in a repurposed `squid/core/pagination.py` (the `SignedCursor` module becomes a
tiny constants module importable by both `squid/api` and `squid/search/domain`, which already imports
`squid.core.errors`).

The dataset is ~10^4–10^5 rows; an `OFFSET 10000` scan over an indexed table of that size is single-digit
milliseconds, and the ranked path is capped at ~200 fused candidates anyway. Deep full-collection traversal (the
sitemap) uses the unbounded `after_id` keyset chain and is unaffected by the clamp.

Enforced two ways so no path can 5xx: FastAPI `Query(ge=0, le=10_000)` turns out-of-range ints into 422 for the REST
surface, and `SearchRequest.__post_init__` raises `ValidationError` for the Discord bot path.

### Envelope

In `squid/api/pagination.py`:

```python
class PageAnchor(BaseModel):
    """Plain query-parameter values addressing an adjacent page. Exactly one field is set."""
    model_config = ConfigDict(extra="forbid")
    offset: int | None = None
    after_id: int | None = None
    before_id: int | None = None

class Page[ItemT](BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemT]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None
```

**Anchor-mode rule** (documented in the module docstring): responses mirror the addressing mode of the request.
Offset-addressed requests — explicit `offset`, any non-default sort, or ranked search — get `{"offset": n}` anchors.
Keyset-capable requests under the default `-id` order, including the parameterless first page, get
`{"after_id": n}` / `{"before_id": n}` anchors. This keeps the sitemap crawl on the unbounded keyset chain.

### Keyset semantics

Display order is `id DESC` for catalogue endpoints, preserving today's behavior.

- `after_id=x` → `WHERE id < x ORDER BY id DESC LIMIT n+1`; the overflow row trims the tail;
  `next = after_id(last_id)` if overflow, `prev = before_id(first_id)` always (you came from somewhere).
- `before_id=x` → `WHERE id > x ORDER BY id ASC LIMIT n+1`, reversed in memory; the overflow row trims the front;
  `prev = before_id(first_id)` if overflow, `next = after_id(last_id)` always.
- No anchor → `prev = None`, `next` if `has_more`.
- More than one of `offset`/`after_id`/`before_id` → `ValidationError(code=ErrorCode.INVALID_QUERY)` → 400.

### Sorted-search facet dedup

The critical correctness fix, at `squid/search/infrastructure/repository.py:118-169`. The raw
`outerjoin(facet, ...)` duplicates a document once per multi-value facet row under the
`(document_id, field_name, ordinal)` uniqueness scheme, corrupting OFFSET math. Replace it with an aggregated
one-row-per-document subquery join:

```python
storage_name = field.storage_name or field.name
value_source = {
    FieldType.TEXT: func.lower(SearchDocumentFacet.text_value),
    FieldType.NUMBER: SearchDocumentFacet.numeric_value,
    FieldType.TIMESTAMP: SearchDocumentFacet.timestamp_value,
    FieldType.BOOLEAN: SearchDocumentFacet.boolean_value,
}[field.value_type]
ascending = request.sort is None or request.sort.direction is SortDirection.ASCENDING
if field.value_type is FieldType.BOOLEAN:  # Postgres has no min/max over boolean
    aggregate = func.bool_and(value_source) if ascending else func.bool_or(value_source)
else:
    aggregate = func.min(value_source) if ascending else func.max(value_source)
anchor = (
    select(SearchDocumentFacet.document_id.label("document_id"), aggregate.label("sort_value"))
    .where(SearchDocumentFacet.field_name == storage_name)
    .group_by(SearchDocumentFacet.document_id)
    .subquery("sort_anchor")
)
statement = (
    select(SearchDocument)
    .outerjoin(anchor, anchor.c.document_id == SearchDocument.id)
    .where(predicate)
    .order_by(
        anchor.c.sort_value.is_(None),           # facet-less documents last, as today
        anchor.c.sort_value.asc() if ascending else anchor.c.sort_value.desc(),
        SearchDocument.normalized_title,
        SearchDocument.resource_kind,
        SearchDocument.source_key,
    )
    .offset(offset)
    .limit(request.page_size)
)
total = await session.scalar(select(func.count()).select_from(SearchDocument).where(predicate)) or 0
```

`GROUP BY` guarantees one join row per document, so OFFSET and ordering are exact; ascending anchors on the minimum
value and descending on the maximum. The COUNT runs against `SearchDocument` alone — the compiled predicate is
self-contained, as `_filter_only` proves by using it join-free today — so totals need no facet join. The
`_CANDIDATE_LIMIT` cap is removed from the sorted path (real OFFSET paging) and stays on `_ranked`.

### Search domain shapes

- `SearchRequest`: `cursor: str | None` → `offset: int = 0`; `__post_init__` validates
  `0 <= offset <= MAX_PAGE_OFFSET`. Delete `CursorPosition`.
- `SearchPage`: `{hits, total: int, next_offset: int | None, prev_offset: int | None, warnings, generated_at}`.
  `has_more` is derivable; the bot computes "Page N of M" from `request.offset` and `total`.
- `SearchSlice` (`application/ports.py`): `{hits, total: int, warnings}`;
  `SearchBackend.search(self, request, query, *, offset: int) -> SearchSlice`.
- `SearchService.__init__` drops the `cursors` parameter. `search()` computes
  `next_offset = offset + page_size if offset + page_size < total else None` and
  `prev_offset = max(offset - page_size, 0) if offset > 0 else None`.
- `_ranked`: `total = len(fused)`; slice `fused[offset : offset + page_size]`; fetch documents only for the slice.
  Honest at the cap — `next_offset` is None past the end of the fused list even though the DB may hold more. Delete
  `_ranked_start`, `_last_position`, `_cursor_title` (this also kills the deleted-anchor silent-empty-page bug at
  :234-236) and all `request_hash` machinery; grep confirmed nothing else uses it.

### Catalogue sort

A shared `parse_page_sort(value, *, allowed: frozenset[str], default: str) -> tuple[str, bool]` (field, descending)
in `squid/api/pagination.py`, using the same `-` prefix convention as search;
`squid/api/v1/search.py::parse_sort` is refactored on top of it, mapping the bool to `SortDirection`.

Allowlists, from model inspection: **builds** `{id, submission_time}` (`id` is the PK and
`idx_builds_submission_time` exists at `squid/builds/infrastructure/models.py:94`); **records** `{id}` only, since
`RecordResult.computed_at` has no index and `id` is a strict proxy for computation recency. The default for both is
`-id`, exactly today's `ORDER BY id DESC`. A non-default sort combined with `after_id`/`before_id` is a 400
`INVALID_QUERY`. Builds sorted by `submission_time` get an `id DESC` tie-break and `NULLS LAST`, since the column is
nullable.

### Config tombstone

Removing the `cursor` config field makes a still-set `SQUID_CURSOR_SECRET` an unknown key — normally a warning, but
a **boot failure** under `SQUID_STRICT_UNKNOWN_KEYS=true`, which the fuzz environment sets and deployments may.
Mitigation: `_RETIRED_ENVIRONMENT_KEYS = frozenset({"SQUID_CURSOR_SECRET"})` unioned into `known` inside
`_audit_unknown_environment_keys` (`squid/config.py:1119`), plus the `.env.example` removal, so lingering env vars
are ignored gracefully in both modes.

`ErrorCode.INVALID_CURSOR` (`squid/core/errors.py:35`) is removed; every raiser dies with the codecs. It appears in
`contracts/openapi.json:12547`, so the contract regeneration picks it up.

## Commit 1 — `builds,records,notifications: add totals and bidirectional page queries`

Purely additive; the tree stays green with the old endpoints untouched.

- `squid/builds/application/queries.py` — `BuildQueryRepository` and `BuildQueryService`:

  ```python
  @dataclass(frozen=True, slots=True)
  class BuildListSort:
      field: Literal["id", "submission_time"] = "id"
      descending: bool = True

  async def list_page(self, *, statuses, submitter_id=None, submitter_account_id=None,
                      sort: BuildListSort = BuildListSort(), offset: int = 0,
                      after_id: int | None = None, before_id: int | None = None,
                      limit: int = 21) -> list[Build]
  async def count(self, *, statuses, submitter_id=None, submitter_account_id=None) -> int
  ```

- `squid/builds/infrastructure/repository.py::list_page` (:131) — before-queries flip the comparison
  (`id > before_id`) and the ORDER BY, then reverse in memory; offset mode uses `.offset(...)`; the new `count()`
  reuses the same status/submitter predicate through an extracted `_page_filter` helper.
- `squid/records/application/ports.py:38`, `services.py:266`, `infrastructure/repository.py:321-343` —
  `list_active_records(*, offset=0, after_id=None, before_id=None, descending=True, limit)` and
  `count_active_records() -> int` (COUNT over `RecordResult JOIN run WHERE is_active`).
- `squid/notifications/application.py:137` and `infrastructure/repository.py:232` —
  `inbox(..., offset=0, after_id=None, before_id=None, ...)` and `count_inbox(account_id, *, include_staff) -> int`,
  cheap under `notifications_account_inbox_idx (account_id, id)` at `models.py:119`.
- Unit tests for the new service passthroughs where such tests exist.

## Commit 2 — `api,search: replace signed cursors with transparent pagination`

The irreducible contract flip. `Page` is shared by all eight list endpoints and the contract test
(`tests/unit/api/test_openapi_contract.py:70`) enforces byte-equality, so the envelope, every endpoint, the search
core, the bot view, and the regenerated `contracts/openapi.json` must land together. Splitting it further would
leave broken intermediate trees.

**Deletions**: the contents of `squid/core/pagination.py` (module repurposed for `MAX_PAGE_OFFSET`),
`squid/search/application/cursor.py`, `CursorPosition` from `squid/search/domain/models.py` and both `__init__.py`
export lists, `cursor_signer`/`CursorSigner` from `squid/api/dependencies.py:85-96` (fold the loose `PageSize` alias
at :91 into `squid/api/pagination.py`), `ErrorCode.INVALID_CURSOR`, `tests/fuzz/fuzz_cursor_codec.py`,
`tests/fuzz/corpus/cursor_codec/`, and the `cursor_codec` entry in `scripts/run_fuzz_target.py:33` `TARGETS` — fuzz
harnesses are registered there and exercised by `tests/unit/test_fuzz_runner.py`, whose parametrized cases at
:22-25 and :43-52 must switch to `search_parser`/`version_parser`.

**`squid/api/pagination.py`** (new machinery replacing ~6 copy-pasted decode helpers):

```python
MAX_PAGE_OFFSET  # re-exported from squid.core.pagination
PageSizeParam = Annotated[int, Query(ge=1, le=50, description=...)]
OffsetParam   = Annotated[int | None, Query(ge=0, le=MAX_PAGE_OFFSET, description=...)]
AfterIdParam  = Annotated[int | None, Query(ge=1, description=...)]
BeforeIdParam = Annotated[int | None, Query(ge=1, description=...)]

@dataclass(frozen=True, slots=True)
class PageSelector:
    offset: int = 0
    after_id: int | None = None
    before_id: int | None = None

def resolve_selector(*, offset, after_id=None, before_id=None, keyset_allowed=True) -> PageSelector
    # >1 provided, or an anchor with keyset_allowed=False → ValidationError(code=ErrorCode.INVALID_QUERY)
def parse_page_sort(value, *, allowed: frozenset[str], default: str) -> tuple[str, bool]
def offset_page[RawT, ItemT](rows: Sequence[RawT], *, offset, page_size, render) -> Page[ItemT]
    # in-memory: total=len(rows); next={"offset": offset+page_size} if < total;
    # prev={"offset": max(offset-page_size, 0)} if offset > 0
def keyset_page[RawT, ItemT](rows, *, selector, page_size, total, id_of, render) -> Page[ItemT]
    # rows = limit+1 overfetch already in display order; implements the anchor rules above
```

**Endpoints** — uniform `page_size: PageSizeParam`, `offset: OffsetParam`, plus `after_id`/`before_id` where ids are
stable:

- `squid/api/v1/builds.py` — delete `after_id_from_cursor` (:220) and rewrite `keyset_page` (:199) on the shared
  helper. `list_builds`: the q-path rejects `after_id`/`before_id` (ranked results are offset-only), passes
  `SearchRequest(offset=offset or 0, ...)`, and wraps `SearchPage.next_offset`/`prev_offset` into offset anchors;
  the catalogue path resolves `sort` via `parse_page_sort(allowed={"id", "submission_time"}, default="-id")`, drops
  the :166 "sort only with q" 400, and calls `list_page` + `count`. The status/q exclusivity check stays.
- `squid/api/v1/me.py` — replace the `after_id_from_cursor`/`keyset_page` imports with the shared helpers; offset
  plus anchors, no sort.
- `squid/api/v1/records.py` — delete `_after_id` (:67); sort allowlist `{"id"}`; anchors, offset, count.
- `squid/api/v1/notifications.py` — delete the inline `_after_id` (:178); offset, anchors, `count_inbox`.
- `squid/api/v1/tags.py`, `versions.py`, `schematics.py` — delete the `_offset` helpers; `offset_page(...)` over the
  in-memory lists, offset-only, no anchors.
- `squid/api/v1/search.py` — `cursor` → `offset: OffsetParam`; `Page(items=..., total=result.total, ...)` with
  offset anchors.

**Search core**: the domain, ports, service, and repository changes described above; `squid/bootstrap.py:460` drops
the `CursorCodec(...)` argument and the import at :87 is trimmed.

**Bot** (`squid/bot/submission/search_view.py`): delete `_cursor_history`; `can_go_back`/`can_go_forward` read
`self._page.prev_offset`/`next_offset`; `next_page`/`previous_page` use
`dataclasses.replace(self._request, offset=...)`, which also fixes the current positional-construction fragility;
the header msgid becomes `"-# Page {page} of {pages}"` with `page = offset // page_size + 1` and
`pages = max(1, math.ceil(total / page_size))`. Run `just i18n-update` to refresh `locales/squid.pot` and the `.po`
files, leaving the zh_CN translation fuzzy for a human.

**Contract**: `just export-openapi` (runs `scripts/export_openapi.py` → `contracts/openapi.json`), committed in this
same commit so `test_committed_openapi_document_matches_application` passes.

**Tests in this commit**:

- Delete `tests/unit/core/test_pagination.py`, `tests/unit/search/test_cursor.py`, and the `codec` fixture in
  `tests/unit/search/conftest.py`.
- New `tests/unit/api/test_pagination.py`: mutual exclusion → 400, `keyset_page`/`offset_page` anchor math in both
  directions, `parse_page_sort` allowlist rejection, offset above `MAX_PAGE_OFFSET` → 422 at a real route.
- Rewrite `tests/unit/search/test_service.py` (the fake backend takes `offset` and returns
  `SearchSlice(hits, total, warnings)`; assert next/prev offset computation and offset validation),
  `tests/unit/search/test_models.py` (offset bounds alongside page_size), `tests/unit/api/test_search_routes.py`
  (mock returns the new `SearchPage`), `tests/unit/api/test_authoritative_build_views.py` (the :190 cursor-cross-view
  and :241 submitter-binding tests are obsolete by design — replace with after_id/before_id direction tests and
  mutual-exclusion/anchor-with-sort 400 tests), the `tests/unit/api/test_phase2_reads.py:74` region (envelope is now
  `items/total/next/prev`), `tests/unit/schematics/test_public_api.py:61` (drop `SignedCursor`, pass `offset`),
  `tests/unit/api/test_notifications.py` (drop SIGNER), and `tests/unit/test_fuzz_runner.py`.
- `tests/unit/api/fakes.py` keeps its `"cursor"` config key until commit 3; the config field still exists here, so
  this keeps the commit green.

## Commit 3 — `config: retire the pagination cursor secret`

- `squid/config.py`: delete `CursorConfig` (:278-289), `RuntimeConfig.cursor_secret` (:815), the mapping (:917),
  `cursor: CursorConfig` in `_ProcessSettings` (:864), and `"cursor"` in the three include-sets (:1006, :1037,
  :1065); add the `_RETIRED_ENVIRONMENT_KEYS` tombstone in `_audit_unknown_environment_keys`.
- `.env.example:23`: delete `SQUID_CURSOR_SECRET`; grep confirmed `compose.yml` and `deploy/` have no references.
- `tests/fuzz/api/environment.py`: remove `cursor_secret` from both secret dataclasses (:160, :185) and the
  `SQUID_CURSOR_SECRET` env entry (:313). This environment sets `SQUID_STRICT_UNKNOWN_KEYS=true`, so this removal
  and the tombstone are both load-bearing.
- `tests/unit/test_config.py`: drop the :162 assertion, remove `"cursor"` from the required-fields set (~:546),
  delete `test_cursor_secret_requires_enough_entropy_material` (:528), and add a test that a lingering
  `SQUID_CURSOR_SECRET` boots cleanly under strict mode.
- `tests/unit/api/fakes.py:35`: drop the `"cursor"` key.

## Commit 4 — `tests: cover offset pagination against live PostgreSQL`

New `tests/integration/search/test_repository_pagination.py`, following the extension and
`Base.metadata.create_all` harness of `tests/integration/search/test_embeddings.py` (needs `search_documents` and
`search_document_facets`):

- ranked: offset paging is stable across requests; `total == len(fused)`; `next_offset is None` at the candidate cap
  even when more rows exist.
- sorted: a document with two facet values for the sort field appears exactly once; totals and OFFSET math are
  unaffected; ascending orders by the minimum value and descending by the maximum; facet-less documents sort last.
- filter-only: SQL offset/limit and COUNT parity, with no anchor lookups.

Plus a builds-repository test (harness: `tests/integration/builds/conftest.py::migrated_session_factory`) for
`before_id` flip-and-reverse ordering and `count()` parity with `list_page` totals. This discharges `TODO.md:32`.

## Commit 5 — `web: adopt transparent pagination params`

- Regenerate types: `cd web && bun run sdk:generate` (reads `../contracts/openapi.json` per `openapi-ts.config.ts`).
  The giant `PageAnnotatedUnion…` alias imported in `api.ts` may be renamed — fix the import; a `PageAnchor` type
  appears.
- `web/src/lib/api.ts`: `BuildQuery` becomes `{q?, sort?, offset?, afterId?, beforeId?, pageSize?}` mapped to
  `offset`/`after_id`/`before_id`; the same for `fetchRecords` (switch to an options object) and `fetchSearch`
  (`offset` only).
- `web/src/components/Pagination.astro`: props become `{locale, next: PageAnchor | null, prev: PageAnchor | null,
  url}`; build each link by cloning the URL, deleting all three pagination params, then setting the anchor's
  non-null entry; render a Previous link (`rel="prev"`) and a Next link (`rel="next"`). Add `common.previous` (and
  keep `common.loadMore` as the next label) in both locale maps in `web/src/lib/i18n.ts`.
- `web/src/views/{BuildsPage,RecordsPage,CreatorPage,SearchPage}.astro`: read `offset`/`after_id`/`before_id`
  (int-parse, ignore garbage), strip all three from the canonical URL, `noindex` when any is present, and pass
  `page.next`/`page.prev` through.
- `web/src/pages/sitemap.xml.ts:39-58`: follow `page.next` anchors (`after_id` for builds and records) until null,
  instead of `next_cursor`.
- Tests: `web/tests/fixtures/api.ts` (serve `after_id`/`offset`-addressed mock pages with `total/next/prev`),
  `web/tests/unit/api.test.ts` (envelope fixtures), `web/tests/unit/i18n.test.ts:26` (assert `?offset=20`-style
  params survive locale switching), `web/tests/e2e/catalogue.spec.ts` (:22-27 locale-switch URL, :54-57 pagination
  link now matches `after_id=`/`offset=`).

## Verification

Run the smallest set that covers each commit, one directory at a time — a broad `pytest tests/unit` run has OOM-ed
this machine.

- Per commit: the focused pytest selection listed above, plus `uv run ruff check` and `uv run basedpyright` over the
  changed files.
- Contract: `uv run pytest tests/unit/api/test_openapi_contract.py --no-cov` after `just export-openapi`.
- `uv run alembic heads` (single head, unchanged — no migrations here) and `git diff --check`.
- `just i18n-update` output committed with commit 2.
- Web: `cd web && bun run sdk:check && bun run check && bun run lint && bun run test && bun run test:e2e`;
  `sdk:check` proves the generated types match the committed contract.
- Defer the full suite and the integration tests (Docker/testcontainers) to CI.

## Risks

- **Offset clamp (10,000)**: deep offset access to the largest collections becomes deliberately impossible; keyset
  anchors cover full traversal. If the dataset grows 10x, only the constant needs revisiting.
- **COUNT on the notifications inbox**: one COUNT per inbox request, mitigated by the index-only-scan-friendly
  `notifications_account_inbox_idx (account_id, id)` and small per-account row counts. The same pattern for builds
  and records is bounded by the ~10^5 dataset.
- **Lingering `SQUID_CURSOR_SECRET` in deployments**: handled by the retired-key tombstone; without it, strict-mode
  deployments and the API fuzz container fail to boot. This is the sneakiest breakage in the plan, and the tombstone
  test in commit 3 pins it.
- **`hydrate_builds` drops stale hits**: a search REST page can return fewer than `page_size` items while `total`
  still counts them (`squid/api/v1/search.py:79-83`). Accepted; worth a code comment.
- **Unsigned anchors allow id probing**: `after_id` values are now user-forgeable across views, but every list query
  ANDs the anchor into an authorization-scoped predicate (the status gate at `builds.py:174`,
  `submitter_account_id` in me-builds, `account_id` in the inbox), so nothing leaks. Only the old "cursor belongs to
  another collection" 400s disappear. The replacement tests in commit 2 document this reasoning.
- **Old cursors in the wild** 400/422 as unparseable ints. Accepted: the `cursor` query parameter disappears from
  the contract entirely, which is breaking for stale clients by design.
- **Ranked-cap honesty**: at `_CANDIDATE_LIMIT`, `total` undercounts the true corpus and `next` ends at the fused
  list. Documented in the backend docstring and pinned by the ranked integration test.
- **Generated TS type-name churn**: the search `Page…` union alias name is derived from the Pydantic generic and
  will change with the envelope; commit 5 must chase the renames in `api.ts` imports.
- **Boolean facet sort** uses `bool_and`/`bool_or` because Postgres lacks `min`/`max` over boolean — semantically
  identical, covered by the sorted-path integration test.
- `docs/plans/rest-api.md` describes the signed-cursor design historically; commit 2 adds a one-line supersession
  note rather than rewriting it.

## Status

- Commit 1: landed.
- Commit 2: landed, with the page-assembly layering above folded in. `parse_page_sort` allows identifier anchors in
  either direction (the rule is "ID anchors require an ID ordering", which is what the repositories actually
  enforce), rather than only in the default descending order.
- Commit 3: landed.
- Commit 4: landed. `tests/integration/search/test_repository_pagination.py` and
  `tests/integration/builds/test_build_pagination.py` both run green against testcontainers PostgreSQL.
- Commit 5: landed. The web catalogue reads `offset`/`after_id`/`before_id` through `web/src/lib/pagination.ts`,
  renders Previous and Next links, and crawls the sitemap along `after_id` anchors. `bun run test:e2e` could not be
  verified locally -- Playwright's browsers are not installed on this machine -- so it is left to CI.

Note for future runs on this machine: a broad `pytest tests/unit` OOM-ed it once, and
`tests/unit/api/test_openapi_fuzz.py` (Schemathesis) is slow enough to look like a hang. Run suites a directory or
two at a time with `--no-cov`, and give the fuzz module its own run.

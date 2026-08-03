# Integrating Nucleation into Redstone-Squid

> **Status.** Phases 0 and 1 have landed. Phases 2-4 are not started. Everything below the
> status block is the plan as approved, amended in place wherever building it proved part of it
> wrong; those amendments are called out where they occur rather than silently applied.

## Context

Redstone-Squid manages Minecraft redstone build submissions, but it has **zero schematic
awareness**. Every dimension is typed by hand and never verified; `world_download_urls`
are opaque strings the bot never opens; build cards depend on users remembering to attach
a screenshot; duplicate submissions are caught only by human memory.

[Nucleation](https://github.com/Schem-at/Nucleation) is an MIT-licensed Rust schematic
engine with native Python bindings (`nucleation==0.9.2` on PyPI, cp312-abi3 wheels built with
scikit-build-core and nanobind — its docs say PyO3, but the published wheel is not). It parses
`.litematic` / `.schem` / `.mcstructure` / world files, computes translation- and
rotation-invariant structural fingerprints, renders headless PNGs, simulates redstone via
MCHPRS redpiler, and detects the repeating lattice of a tiling build.

The outcome: schematic attachments become first-class. Dimensions get machine-verified,
duplicate reposts get flagged before voting, build cards get auto-rendered previews, and
`TODO.md:10-13` (expandable-volume records) gains a real source of lattice data.

**Scope honesty — read before starting:**

- This does **not** close `TODO.md:8`. `block_count` is non-air blocks; Door Rules
  cumulative volume includes air pockets and has hallway/frame/hitbox exceptions.
  `RecordCandidate.fixed_volume` (`squid/records/infrastructure/repository.py:594`) must
  **not** be repointed at `block_count` in any phase here. The win is verified
  `width × height × length`, plus `block_count` as new, separate evidence.
- Phase 4 timing measurement is **gated on a verification experiment** (§Phase 4). Redpiler
  is a redstone-logic compiler; its piston-movement fidelity decides whether door timing is
  measurable at all.

---

## Engine API corrections (verified against the installed wheel, 2026-08-03)

`docs/api-reference-python.md` upstream describes a **polished wrapper that the PyPI wheel
does not ship**. The wheel exposes the lower-level *generated (Diplomat) bindings*. All of the
capability is there, but the shapes differ. Verified by running against `nucleation==0.9.2`:

Reported upstream with reproducers: [Schem-at/Nucleation#3][n3] (the mismatch itself),
[#4][n4] (`from_data`), [#5][n5] (`dimensions()`), [#6][n6] (fingerprint presets). Re-check
these before assuming any row below still holds after a version bump.

[n3]: https://github.com/Schem-at/Nucleation/issues/3
[n4]: https://github.com/Schem-at/Nucleation/issues/4
[n5]: https://github.com/Schem-at/Nucleation/issues/5
[n6]: https://github.com/Schem-at/Nucleation/issues/6

| Docs claim | Reality in the wheel |
|---|---|
| `Schematic.new(name)` | `Schematic.create(name)` |
| `schem.fingerprint(preset)` | `Fingerprint.compute(schem, preset)` — also `Fingerprint.is_duplicate(a, b, preset)`, `.footprint_distance(a, b, preset)`, `.signature_json` |
| `schem.diff(other, preset)` | `Diff.compute(a, b, preset)` → `.distance`, `.support`, `.summary_json()`, `.markers()`, `.added/.removed/.changed/.swapped` |
| `schem.detect_structures()` | `Autostack.detect_structures(schem)` → **JSON string**; `Autostack.resize_1d(schem, vx, vy, vz, units)` |
| `schem.simulate(...)`, `schem.is_lit(...)` | `MchprsWorld.create(schem)` → `.on_use_block`, `.is_lit`, `.get_signal_strength`, `.get_lever_power`, `.flush` |
| `schem.render(path, ...)` | `Renderer.render_png_b64_with_pack(schem, pack, config)`; `RenderConfig.create()` + `.set_isometric()` / `.set_sphere_fit()` / `.set_background()`; `ResourcePack.from_bytes(data)` |
| `to_litematic() -> bytes` | `to_litematic_b64() -> str` — **base64**, must be decoded. Same for `glb_data_b64`, `render_png_b64`, `to_schematic_b64`, `to_mcstructure_b64` |
| structured returns | `*_json` suffix returns **JSON strings**: `region_names_json()`, `extract_signs_json()`, `compile_insign_json()`, `get_entities_json()`, `palette_json()` |
| import methods "mutate the existing instance" | **False.** `from_data` / `from_litematic` are *constructors that return a new `Schematic`*. Ignoring the return value silently yields an empty 0×0×0 schematic — this cost real debugging time |
| `dimensions` is "tight content dimensions" | **False.** `dimensions()` returns loose/allocated bounds (a 3×4×1 build reported 3×68×1). **`tight_dimensions()` is the authoritative one** and is what `SchematicMetrics.dimensions` must use |
| all properties | Most are **methods**, not properties: `block_count()`, `volume()`, `dimensions()`, `source_data_version()` |

Confirmed working as the plan assumes:

- **Fingerprint translation-invariance holds** for all three presets — the same build placed at
  (0,0,0) and (13,0,−7) compares equal. This is the load-bearing Phase 2 assumption.
- **Autostack returns exactly the shape `AutostackLattice` models** — `{mode, vectors, coverage,
  region_min, region_max, cell_min, cell_max, label}`, and correctly recovered a 4-block period.
- Round-trip through `to_litematic_b64` → `from_data` preserves dimensions, block count, and
  the `exact` fingerprint.
- Importing the engine emits **no warnings**, so `filterwarnings = ["error"]` is not a problem.
- **Headless rendering works.** On a box with no GPU access (`/dev/dri/*` permission denied),
  `Renderer.render_png_b64` still produced a valid PNG by falling through to software Vulkan;
  `lvp_icd.json` + `libvulkan_lvp.so` (lavapipe) were already present. This closes the largest
  Phase 3 risk. Note the renderer takes the **pack zip bytes directly**
  (`render_png_b64(schematic, pack_zip: Sequence[int], config)`) and there is no
  render-without-pack path, which confirms the operator-supplied-pack requirement.

### Phase 2 preset choice — corrected

The plan originally indexed duplicates on the `structural` preset. **That is wrong.**
Measured: a build differing by one added glass block still reports
`Fingerprint.is_duplicate(..., "structural") == True`. `structural` is a coarse bucket, not an
identity.

Use **`shape`** as the primary duplicate index (translation- *and* rotation-invariant, but
correctly reports `is_duplicate == False` at `footprint_distance == 0.726` for the one-block
difference), with `exact` as the strict tier. Keep `structural` only as a cheap coarse
pre-filter feeding the pairwise ranking, never as a standalone "this is a duplicate" verdict.

---

## Architecture decisions

| Question | Decision |
|---|---|
| Where does this live? | New bounded context **`squid/schematics/`**, not inside `builds` |
| Where is `import nucleation` allowed? | Only `squid/schematics/infrastructure/nucleation_adapter.py` + `worker_main.py` |
| How are sync native calls offloaded? | **Supervised `spawn` subprocess worker pool** we own |
| Where do schematic bytes live? | `schematic_files.data bytea`, content-addressed by SHA-256 |
| Hard dependency? | **No** — optional extra `schematics`; bot degrades to today's behavior |

A new context is right because the capability is build-agnostic (`records`, `search`, and
`squid/api` will all want it) and because putting a GPU pool and a subprocess supervisor
inside `builds/infrastructure` — already 9 modules and a 652-line repository — is wrong.
It mirrors the existing `squid/versions/` precedent: a small context that `builds` reaches
through a narrow port it declares itself (`DefaultVersionResolver`,
`squid/builds/application/ports.py:43`).

### Why a subprocess pool, not `asyncio.to_thread`

`asyncio.to_thread` is fine for the existing `vecs` calls
(`squid/builds/infrastructure/embeddings.py:57,71`) but not here:

1. **No cancellation.** `wait_for` returns control but the thread runs on. A pathological
   `simulate()` pins a default-executor thread forever — an executor shared with embeddings.
2. **Process-fatal failures.** wgpu, rayon, and MCHPRS spawn their own threads. A panic
   there, or a `panic = "abort"` profile, takes the whole bot down.
3. **wgpu global state** is process-global and not fork-safe.

`ProcessPoolExecutor` is worse: one crash raises `BrokenProcessPool` and poisons the entire
pool with no respawn. The owned supervisor gives **per-worker automatic crash recovery**,
which is the whole point.

---

## Layout

```
squid/schematics/
  errors.py                    # SchematicError hierarchy (subclasses squid.core.errors)
  domain/
    models.py                  # frozen value objects
    formats.py                 # magic-byte sniffing + inflation budget (stdlib only)
  application/
    ports.py                   # SchematicAnalyzer, SchematicStore, SchematicVersionResolver
    services.py                # SchematicService
    commands.py                # AnalyzeRequest / RenderRequest / SimulationRequest DTOs
  infrastructure/
    models.py repository.py mapping.py
    nucleation_adapter.py      # THE ONLY module that imports nucleation
    worker.py                  # SchematicWorkerPool (supervisor, asyncio side)
    worker_main.py             # python -m squid.schematics.infrastructure.worker_main
    threaded.py                # dev/test fallback, never used with render/simulate
    capability.py resource_pack.py version_resolver.py
```

### Domain value objects (`domain/models.py`)

All frozen, slotted, stdlib-typed. Nothing from nucleation crosses this line.

`SchematicFormat` (StrEnum), `SchematicDimensions`, `SchematicSign`,
`SchematicFingerprints(structural, shape, exact, signature_structural)`,
`AutostackLattice(mode, vectors, coverage, cell_min/max, region_min/max, label)`,
`VersionLossEntry(version, kind, severity, path, detail)`,
`SchematicMetrics(source_format, byte_size, sha256, dimensions, allocated_dimensions,
block_count, bounding_volume, entity_count, palette_size, region_names,
source_data_version, declared_name, declared_author, signs)`, and

```python
@dataclass(frozen=True, slots=True)
class SchematicAnalysis:
    metrics: SchematicMetrics
    fingerprints: SchematicFingerprints
    lattice: AutostackLattice | None
    analyzer_version: str          # "nucleation-0.9.2"
    analysis_schema_version: int   # ours, bumped when we change what we compute
```

`analyzer_version` is load-bearing: **fingerprints are not stable across nucleation
upgrades.** Every persisted fingerprint carries its producer's version and duplicate
lookups filter on it, so a version bump becomes a backfill job rather than a silent
correctness regression.

### Ports (`application/ports.py`)

```python
class SchematicAnalyzer(Protocol):
    async def analyze(self, data: bytes, *, limits: SchematicLimits,
                      with_lattice: bool = False) -> SchematicAnalysis: ...
    async def convert(self, data: bytes, *, target: SchematicFormat,
                      data_version: int | None = None
                      ) -> tuple[bytes, tuple[VersionLossEntry, ...]]: ...
    async def compare(self, left: bytes, right: bytes, *,
                      preset: FingerprintPreset) -> SchematicComparison: ...
    async def render(self, data: bytes, *, request: RenderRequest) -> bytes: ...
    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult: ...
    async def autostack(self, data: bytes, *, lattice: AutostackLattice,
                        counts: tuple[int, ...]) -> bytes: ...
    async def capabilities(self) -> AnalyzerCapabilities: ...
```

`SchematicStore` (implemented by `SchematicRepository`): `put_file`, `get_file`,
`record_analysis`, `list_for_build`, `get_primary`, `find_fingerprint_matches`,
`find_metric_neighbours`, `record_render`.

`builds` gains exactly **one** narrow port in `squid/builds/application/ports.py` so
`BuildService` never learns what a schematic is:

```python
class BuildSchematicSummaryProvider(Protocol):
    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None: ...
```

backed by `squid/builds/infrastructure/schematic_summary.py`, which delegates to
`SchematicService`. Everything else (`/build schematic *`, dup checks, render triggering)
goes from the bot straight to `services.schematics` — legal, since `squid.bot*` may import
`squid.*.application*`.

### Wiring

- `squid/runtime.py` — `ApplicationServices` gains `schematics: SchematicService`.
- `squid/bootstrap.py` — `create_schematic_analyzer(config.schematics)` returns
  `Null` / `Threaded` / `Subprocess`. `NullSchematicAnalyzer` raises
  `SchematicSupportUnavailableError` from every method when
  `importlib.util.find_spec("nucleation") is None`. `ApplicationRuntime.close_resources`
  currently gets `database.close` directly (`squid/bootstrap.py:115`) — change to a composed
  closer awaiting `worker_pool.aclose()` first.
- `squid/persistence/__init__.py` **must** import the new models module, or `Base.metadata`
  misses the tables and `alembic autogenerate` emits a drop.

### Worker protocol (`infrastructure/worker.py` + `worker_main.py`)

- Spawned via `asyncio.create_subprocess_exec(sys.executable, "-m", ...)`,
  `start_new_session=True`.
- Length-prefixed frames on stdin/stdout: 4-byte BE length + JSON header + raw binary body
  appended (never base64 megabytes). Child stderr piped to the `squid.schematics.worker`
  logger.
- One in-flight request per worker (`asyncio.Lock`), `Semaphore(workers)` fronting the
  pool, an extra `Semaphore(1)` on render because there is one GPU.
- Per-op deadlines from config: `parse=5s, compare=15s, convert=15s, render=45s,
  simulate=90s`. Timeout → `proc.kill()`, respawn, raise `SchematicTimeoutError`. EOF →
  log exit code + stderr tail, respawn, raise `SchematicWorkerCrashedError`.
- Child guardrails set **before** importing nucleation: `RLIMIT_AS` (2 GiB), `RLIMIT_CPU`,
  `RLIMIT_FSIZE`, `RLIMIT_NPROC`, `faulthandler.enable()`, `os.nice(5)`. Guard with
  `sys.platform` — `resource` and `start_new_session` are POSIX-only.
- Restart is rate-limited with exponential backoff and circuit-breaks to `available=False`
  after N crashes in a window, so a poison payload retried by a user can't fork-bomb the host.
- **A `Schematic` object is never cached across requests.** `simulate()` caches an
  `MchprsWorld` internally and advances the wavefront tick-by-tick across calls; bytes-in /
  result-out prevents that entire bug class by construction. (The `ResourcePack` *is*
  cached in worker globals — that's the main reason for a persistent worker over one-shot
  subprocesses.)

---

## Schema

`schematic_files(sha256 text PK, data bytea, byte_size int CHECK <= 2 MiB, source_format,
created_at)` — content-addressed, so re-submitting the same file is detected before any
analysis runs. Real doors are single-digit KB.

Postgres over catbox-as-store-of-record because `upload_to_catbox`
(`squid/bot/utils/uploads.py:35`) returns the response body with no error handling, catbox's
extension allowlist excludes `.litematic`, and we must **re-read** these bytes for every
re-render, diff, and dup check — an HTTP fetch of attacker-influenced URLs on each one.
Catbox stays for derived, replaceable artifacts: the user-facing download link and rendered
PNGs.

`build_schematics` — `id`, `build_id` FK CASCADE, `file_sha256` FK RESTRICT, `is_primary`,
`original_filename`; metrics columns (`width/height/length`, `allocated_*`, `block_count`,
`bounding_volume`, `entity_count`, `palette_size`, `region_names text[]`,
`source_data_version`, `declared_name/author`, `signs jsonb`); identity columns
(`fingerprint_structural/shape/exact`, `signature_structural`, `analyzer_version`,
`analysis_schema_version`); `lattice jsonb`; audit (`uploaded_by_discord_id`, `analyzed_at`).
`UNIQUE (build_id, file_sha256)`, partial `UNIQUE (build_id) WHERE is_primary`.

Indexes: btree `(fingerprint_structural, analyzer_version) WHERE NOT NULL`, same for
`shape`, plus `(block_count)` and `(build_id)`. Fingerprints are translation-invariant
hashes, so **equality is the only meaningful SQL predicate** — near-duplicate ranking is
pairwise and happens in the worker over a SQL-narrowed shortlist.

Phase 3 adds `schematic_renders(id, build_schematic_id FK CASCADE, recipe_hash, url, width,
height, byte_size, created_at, UNIQUE(build_schematic_id, recipe_hash))`.

### Changes to `builds` (per `docs/new-build-attribute.md`)

1. `squid/builds/domain/models.py:36` — widen
   `MediaTypeLiteral = Literal["image", "video", "world-download", "schematic", "render"]`;
   add `schematic_urls` / `render_urls` fields beside `world_download_urls` (l.199).
   **Verified: `build_links.media_type` is plain `text` with no CHECK constraint
   (`infrastructure/models.py:373`, `alembic/sql/20260728_portable_schema.sql:735`), so
   widening the Literal needs no DDL.**
2. `squid/builds/infrastructure/repository.py:263-274` — add the two new tuples to `all_links`.
3. `squid/builds/infrastructure/mapping.py:100-102` — read them back by `media_type`.
4. `squid/builds/application/editing.py` — `BuildEditPatch` gains the fields + `direct_fields`
   entries; `commands.py::DoorSubmissionInput` gains `schematic_urls: tuple[str, ...] = ()`.
5. `squid/bot/submission/build_handler.py` — a "Schematic" link row in
   `get_metadata_fields()`; `_get_media_urls()` appends the render URL **last** so it only
   becomes `media[0]` (the `discord.ui.Thumbnail`, `squid/bot/utils/components.py:63`) when
   there are no real screenshots.

---

## Attachment handling

Two bugs to fix in `squid/bot/submission/submit.py`:

- **l.158** `assert attachment.content_type is not None` — Discord reports `None` for
  `.litematic`, so this raises `AssertionError` today as an unhandled command error.
  l.159-161 then rejects anything not `image/*` / `video/*`.
- **l.271-273** `infer_build_from_message` silently `continue`s on `content_type is None`,
  so schematics posted in the auto-scraped channels are dropped.

New `squid/bot/submission/attachments.py`:

```python
def classify_attachment(filename: str, content_type: str | None, size: int) -> ClassifiedAttachment
```

Rules in order: (1) size check **before** `attachment.read()`; (2) case-insensitive
extension allowlist `.litematic .schem .schematic .nbt .mcstructure` — extension is the
primary signal, `content_type` advisory only; (3) `image/` or `video/` prefix, falling back
to `mimetypes.guess_type` (already imported at `build_handler.py:5`) when `content_type is
None`; (4) `application/octet-stream` without a schematic extension → unsupported;
(5) everything else → translated `ValidationError` listing accepted extensions. **Never
`assert`.**

`squid/schematics/domain/formats.py` — pure stdlib (`gzip`, `zlib`, `zipfile`, `struct`),
all permitted under `tests/architecture/test_boundaries.py:30-40`:

```python
def sniff_container(data) -> Literal["gzip","zlib","zip","raw-nbt","unknown"]
def sniff_schematic_format(data, *, filename_hint) -> SchematicFormat | None
def inflated_size_at_most(data, limit) -> int   # raises DecompressionBudgetExceeded
```

Decides by container plus root-compound tag names in the first ≤64 KiB inflated
(`Metadata`/`Regions` → litematic; `Palette`/`BlockData`+`Version` → sponge; `Blocks`/`Data`
byte arrays → legacy MCEdit; little-endian `size`/`structure` → mcstructure). A user
renaming `bomb.gz` to `door.litematic` is caught here, **before any byte reaches nucleation**.

Flow: `size check → read() → sniff_container → inflated_size_at_most(64 MiB) → put_file(sha256)
→ analyzer.analyze() in the worker`.

In `submit_form`, prefill `build.width/height/depth` from the analysis (safe — `build =
Build(ai_generated=False)` on l.152, so nothing is set yet) and pre-type them into
`SubmissionModal.dimensions` (`ui/views.py:53`) via `format_dimensions()` from
`squid/bot/submission/parse.py`. On submit, if declared ≠ schematic dims, **keep the declared
value** (the human is authoritative; the export may be cropped) and attach a visible mismatch
line to the vote card and `extra_info`. Silent overwrite would corrupt records.
`record_analysis(build_id, ...)` runs post-persist, once `builds.submit()` has assigned the id.

---

## Phases

Each phase ships and reverts independently.

### Phase 0 — dependency, capability probe, arch guards (no user-visible change) — **done**

Ship this alone first: it makes the boundary un-violatable *before* any adapter code exists.

**New:** `squid/schematics/{errors,domain/models,domain/formats,application/ports}.py`,
`infrastructure/capability.py`, `tests/unit/schematics/`.
**Touched:** `pyproject.toml`, `uv.lock`, `requirements/*`, `tests/architecture/test_boundaries.py`,
`tests/architecture/test_import_surfaces.py`.
**Migration:** none. **Tests:** hypothesis properties on the sniffer + inflation budget (no
nucleation needed), plus the new archrules.

**As built**, two deviations from the sketch above:

- `sniff_schematic_format` takes `max_sniff_bytes` as a parameter rather than reading it off
  `SchematicLimits`, keeping the module free of any dependency beyond `domain/models`.
- The `filename_hint` is a **last resort**, not a tie-breaker: content wins whenever it is
  conclusive, and the hint is consulted only once the bytes are proven to be a valid NBT
  stream whose root compound carries no marker we recognise. That way an unknown future
  format version is still handed to the engine, while arbitrary garbage named `door.litematic`
  is not.

Zip archives are refused by `inflated_size_at_most` rather than having their declared entry
sizes summed — those headers are attacker-written — which matches the plan's "world zips
rejected in all phases" but puts a second refusal below `classify_attachment`.

### Phase 1 — ingest + auto-metrics — **done**

**New:** `nucleation_adapter.py`, `worker.py`, `worker_main.py`, `models.py`, `repository.py`,
`mapping.py`, `version_resolver.py`, `application/{services,commands}.py`,
`squid/builds/infrastructure/schematic_summary.py`, `squid/bot/submission/attachments.py`,
`squid/bot/submission/schematics.py` (new cog: `/build schematic info|download|convert`).
**Touched:** `squid/config.py` (`SchematicConfig`, `SchematicLimits`), `squid/runtime.py`,
`squid/bootstrap.py`, `squid/persistence/__init__.py`, the five `builds` files above,
`squid/bot/submission/{submit,ui/views,build_handler,__init__}.py`.
**Migration:** one revision creating `schematic_files` + `build_schematics`, plus
`versions.data_version smallint null` with an `op.execute` seed for the ~30 Java versions
(needed by `convert`).

Also fix `docs/new-migration.md:10` — it says "update the SQLAlchemy models in
`squid/db/schema.py`", **a path that no longer exists**. Correct it to
"`squid/<context>/infrastructure/models.py`, then register the module in
`squid/persistence/__init__.py`."

**As built**, the following differ from the sketch above.

- **A `wire.py` module was added** to `infrastructure/`. The frame format and the value
  (de)serialisation are needed identically by the supervisor and the child, and putting them in
  `worker.py` would have made `worker_main.py` import the asyncio supervisor it is supervised by.
  Standard library only, no engine import.
- **`analyze()` gained `source_format`** on the port. The service has already sniffed the bytes
  *with the filename hint* by the time it calls the analyzer, and the adapter cannot see the
  filename; passing the conclusion through beats re-deriving a worse one. `render()` likewise
  gained `resource_pack`, and the port gained `aclose()` so `bootstrap` can close any analyzer
  uniformly.
- **`RLIMIT_NPROC` is deliberately not set.** It counts processes per real UID, not per process,
  so on a busy host any value low enough to constrain a fork bomb also stops the engine's rayon
  and wgpu pools from creating threads. `start_new_session=True` plus a process-*group* kill on
  timeout covers runaway children properly; `RLIMIT_AS` covers memory. `RLIMIT_CPU` is set as a
  cumulative backstop that recycles a worker, with the per-operation deadline as the real guard.
- **`/build schematic *` stays registered without the engine** and answers "schematic support is
  not enabled on this instance." The plan said never register it. A command that silently does
  not exist is indistinguishable from a bot outage; one sentence is strictly more informative.
  It is a mixin on `SearchCog` for the same reason `BuildEditCommands` is — the `build` group is
  owned by `BuildCommandGroup`, so a separate cog cannot contribute subcommands to it.
- **Two engine behaviours found by the integration tests, not by reading:**
  `Diff.added()/removed()/changed()` return whole `Schematic` objects rather than counts, so
  `edit_distance` uses `diff.distance()`; and **optional metadata accessors raise rather than
  returning empty** when a format does not carry the field — `author()` on a Sponge `.schem`
  throws `NucleationError.NotFound`. All optional metadata reads are guarded; the load-bearing
  measurements are not.
- **`convert` reports fidelity loss only for litematic output.** `to_litematic_for_version_json`
  returns `{data_b64, loss}` atomically; other targets go through
  `convert_to_data_version(target, source)` for the loss report and then re-encode. The engine
  can only *write* litematic, Sponge `.schem`, and `.mcstructure`; legacy `.schematic` and
  structure `.nbt` are read-only and are refused as download targets.
- **Column docstrings do not reach the database.** `Base.__init_subclass__` extracts them but
  they do not land on the mapped columns — pre-existing behaviour, `users` behaves identically —
  so the migration carries table comments only. Not fixed here; noted so the next person does not
  assume the new columns are self-documenting in Postgres.
- **`versions.data_version` is seeded by `UPDATE`, not `INSERT`.** The version catalogue is
  populated at runtime by the version-tracking task, so the migration annotates whichever Java
  releases the database already knows and leaves the rest null. A missing entry resolves to "no
  known data version" rather than a guess, because a wrong number produces a confidently wrong
  conversion.

### Phase 2 — duplicate detection

Phase 1 already *writes* fingerprints; this adds lookup and surfacing.

1. Exact file dedup on `schematic_files.sha256` → "byte-identical to build #N", done.
2. SQL shortlist `fingerprint_structural = ? AND analyzer_version = ?` — index-backed, covers
   moved/rotated.
3. Fuzzy shortlist `block_count BETWEEN n*(1±t)` + sorted dims within tolerance, `LIMIT 25`.
4. For at most **K=5** candidates, load bytes and run `compare()` in the worker
   (`footprint_distance` + `diff().summary_json()`). Rank by distance, total budget 15 s,
   partial results acceptable.
5. Tiers `identical` / `structural-match` / `near`, thresholds in `SchematicConfig`.

Surfaced as a "⚠ Possible duplicate of #1234 (moved/rotated)" field on the vote card.
**Migration:** possibly one composite index `(analyzer_version, block_count)`.
**Verify first:** whether `signature(preset)` is an LSH-style bucketable prefix or an opaque
hash — if bucketable, step 3 becomes index-backed and far better.

### Phase 3 — rendered previews

Vanilla assets are **not redistributable**: the pack is operator-supplied, mirroring
`CatboxConfig` (`squid/config.py:184`). `SchematicRenderConfig(enabled=False, pack_path,
pack_url, pack_sha256, cache_dir, width=768, height=768, max_block_count=400_000,
max_bounding_volume=2_000_000, timeout_seconds=45.0, background=(0,0,0,0))` with a
`model_validator` requiring a pack when enabled.

**Config naming caveat (verified):** `squid/config.py:292-294` sets
`env_nested_delimiter="_"` with `env_nested_max_split=1`, so two-level nesting like
`schematics.render.*` **will not resolve**. Use a flat config reachable in one split and
validate every new `SQUID_*` name against `tests/unit/test_config.py`.

Pack is fetched once at first use, hash-verified, cached under `cache_dir` (the Dockerfile
already sets a writable `XDG_CACHE_HOME`). `ResourcePack.from_file` is called in the worker
and cached in worker globals.

GPU: `python:3.12-slim` has no adapter. Add an optional Dockerfile layer
(`ARG WITH_SOFTWARE_GPU=0`) installing `mesa-vulkan-drivers libvulkan1` (~120 MB) with
`WGPU_BACKEND=vulkan`. lavapipe is a software Vulkan ICD — correct output, seconds per
frame. Acceptable for a background task, **never** inside an interaction response: renders
are always fire-and-forget after the build posts, then the vote message is edited via the
existing `BuildHandler.update_messages()`.

Degradation ladder — nucleation missing → `/build schematic *` never registered
(check `services.schematics.available` in `squid/bot/submission/__init__.py::setup`);
render disabled/no pack/no adapter → everything else works, render skipped, warned **once**
at WARNING not per request; schematic over the caps → skip with a note, don't attempt;
render crashed/timed out → log and skip, **never retry** (retrying a poison payload is how
you get a crash loop).

Use `RenderConfig.isometric(w, h)` + `set_sphere_fit(True)` + `set_background(0,0,0,0)` for a
rotation-stable transparent PNG that reads on both Discord themes. Recipe hash =
`sha256(pack_sha256 + dims + projection + yaw/pitch/zoom + analyzer_version)`, so a config
change invalidates cached renders and an identical request never re-renders.
**Migration:** `schematic_renders`.

### Phase 4 — simulation + autostack (gated)

Staff-gated via the existing `check_is_trusted_or_staff()`
(`squid/bot/utils/permissions.py`), never automatic. `/build measure-timing`,
`/build detect-lattice`. **Migration:** one `ALTER TABLE build_schematics ADD COLUMN
simulation_evidence jsonb`.

**Input identification**, in order of preference: (1) **Insign sign annotations** via
`compile_insign()` — an existing community convention that round-trips through `.litematic`
and travels with the file forever; respond "this schematic has no Insign annotations" when
empty. (2) **Heuristic**: scan `get_all_blocks()` for levers/buttons; if *exactly one*, use
it; if zero or many, **refuse** — a wrong input yields a confidently wrong number, worse
than no number. (3) Manual `input:"12 5 -3"` flag with a converter beside the existing
`DimensionsConverter`.

**Run this experiment before writing any Phase 4 code:** take three doors with published
community timings (2×2 piston door, seamless 3×3, one slime-block door); for each,
`create_simulation_world()`, `on_use_block()` the lever, `tick(1)` up to 200, reading
`is_powered()` on piston coords and diffing block states each tick. If derived tick counts
match published values within ±1 gt on all three, build the feature. If not, ship **only**
the propagation-delay number, labelled "redstone propagation delay (simulated, not door
timing)".

Either way: **simulated timings never auto-populate `Build.normal_opening_time` /
`normal_closing_time`** — those feed `RecordCandidate.timing_variants` and therefore official
records. They are moderator-facing evidence displayed next to the human-declared value
(`TODO.md:42`).

**Autostack** is much lower risk — geometric analysis, not physics. Run `detect_structures()`
opportunistically in Phase 1 when `block_count` is under a threshold; store the
highest-coverage result in `build_schematics.lattice`; surface as "Repeating unit 3×3×7,
stack vector (0,3,0), coverage 0.97". Verification gate: round-trip
`autostack_resize_1d(v, n_original)` on a real expandable door and assert
fingerprint-identity with the input (the docs claim exactness). The lattice gives the
per-layer delta and direction for `TODO.md:10-13` — genuinely most of the per-layer
expression — but **not** the valid expandable domain, which no static analysis can supply.
Deliver it as structured evidence into the records workstream and stop there.

---

## Abuse and resource safety

| Threat | Cap | Enforced in |
|---|---|---|
| Huge upload | `attachment.size ≤ 2 MiB` | `classify_attachment`, before `read()` |
| Decompression bomb | inflated ≤ 64 MiB, streamed | `domain/formats.py`, before the worker |
| Zip bomb (world zip) | **world zips rejected in all phases** | `classify_attachment` |
| Enormous `allocated_dimensions` | `w*h*l ≤ 20 M`; render ≤ 2 M | worker, right after load |
| Malformed NBT → Rust panic | process isolation + auto-respawn | `worker.py` supervisor |
| Runaway CPU | per-op deadline + `RLIMIT_CPU` | supervisor + `worker_main.py` |
| Runaway memory | `RLIMIT_AS` 2 GiB | `worker_main.py` |
| Spam | `@commands.cooldown` per user; `Semaphore(1)` on render; restart backoff | cog + `worker.py` |
| Poison payload retried | never auto-retry a crashed op; per-process sha256 deny-set | `SchematicService` |

---

## Dependency management

No musl wheel and no linux-aarch64 wheel; the sdist needs a Rust toolchain. `python:3.12-slim`
on x86_64 is fine, but the same Dockerfile built on an arm64 host would fail
`uv sync --locked`. Hence:

```toml
[project.optional-dependencies]
schematics = ["nucleation==0.9.2"]
```

**Exact pin, not a range** — fingerprints and loss reports are version-scoped outputs we
persist, so a bump must be a deliberate act with a backfill plan. Add the same pin to
`[dependency-groups] dev` so CI (x86_64 Linux) always exercises the real adapter. Keep
`requirements/base.txt` vanilla; deployments opt in with `uv sync --extra schematics`.
`Dockerfile` gets `ARG WITH_SCHEMATICS=0` so an arm64 build degrades rather than breaks.

**Correction (Phase 0).** This plan originally claimed `[tool.uv] exclude-newer = "7 days"`
was irrelevant given an exact pin. It is not: `exclude-newer` filters the *index* before
resolution, so `uv lock` failed outright because 0.9.2 was published inside the soak window.
The project also releases faster than that window (0.3.0 to 0.9.2 inside two weeks), so the
global cutoff would pin us several API generations behind the version this integration
targets. Resolved with a per-package carve-out, which is defensible precisely because the
dependency is pinned exactly, its wheel hashes are locked, and no default deployment
installs it:

```toml
[tool.uv]
exclude-newer-package = { nucleation = "2026-08-02T00:00:00Z" }
```

`infrastructure/capability.py` uses `importlib.util.find_spec("nucleation")`, **not** a
try-import — importing a native module is expensive and, if ABI-mismatched, can be fatal.
The real import happens only in `worker_main.py`, in the child, where failure is contained.

---

## Testing

**New archrules** in `tests/architecture/test_boundaries.py`:

```python
def test_native_schematic_engine_stays_in_its_adapter() -> None:
    (archrule("only the schematic adapter may import the native engine")
        .match("squid*").exclude("squid.schematics.infrastructure*")
        .should_not_import("nucleation*").check("squid", only_direct_imports=True))
```

Plus `.should_not_import("nucleation*")` appended to the three existing layer rules
(l.30, l.43, l.55), and an AST test in the style of
`test_application_modules_do_not_read_process_environment_directly` (l.67) asserting the
identifier `nucleation` appears nowhere outside the two allowed modules — belt and braces
against dynamic imports. `test_import_surfaces.py` gains
`("squid.schematics.domain", ("nucleation", "sqlalchemy", "discord"))` and
`("squid.schematics.application", ("nucleation",))`.

**Fixtures** — generate them programmatically at test time via `Schematic.create()` +
`set_block()` + `base64.b64decode(schem.to_litematic_b64())` (per the corrections table
above, not the names the upstream docs use). This avoids committing binary files of
uncertain provenance entirely. Commit a
`<name>.golden.json` of the expected `SchematicAnalysis` alongside: unit tests'
`FakeSchematicAnalyzer` returns the golden, and one integration test asserts the *real*
adapter reproduces it — so a nucleation upgrade that changes fingerprints fails exactly one
test and tells you a backfill is needed.

**Unit (no nucleation):** `tests/unit/schematics/domain/test_formats.py` — hypothesis:
arbitrary bytes never crash the sniffer, gzip bombs always rejected, extension never
overrides content. `application/test_services.py` with `FakeSchematicAnalyzer` +
`FakeSchematicStore`, mirroring the existing `FakeBuildRepository` style in
`tests/unit/builds/application/test_services.py`. `test_attachments.py` table-driven,
including `content_type=None`, `application/octet-stream`, `.litematic` renamed from `.png`,
oversize.

**Integration:** new markers beside the existing `external` (`pyproject.toml:236`):
`schematic` and `render`. Skip via `pytest.importorskip("nucleation")` + capability probe.
The single most valuable test: two fixtures where one is the other translated by (13, 0, −7),
asserting `fingerprint_shape` is equal — `shape`, not `structural`, per the preset correction
above, though the invariance was measured to hold for all three. The second most valuable: feed the corrupt
fixture to the worker and **assert the pool respawns and the bot survives** — that's the
test that proves the isolation design. No golden-image comparison for renders; GPU output
isn't bit-reproducible across drivers.

**Caveat (verified):** `pyproject.toml:227` sets `filterwarnings = ["error", ...]`. If
importing nucleation emits any warning, the whole suite fails. Check on first install and add
a targeted ignore if needed.

---

## Verification

```bash
uv sync --extra schematics                     # confirm the wheel installs on this host
just lint && just typecheck
just test                                      # unit + architecture, no nucleation needed
just db-upgrade && just db-check               # after each migration
just test-integration                          # needs Postgres + nucleation
```

End-to-end per phase, in a test guild:

1. **Phase 1** — `/build submit` with a `.litematic` attached. Confirm: no `AssertionError`;
   dimensions pre-typed in the modal; the info card shows block count, palette size, source
   data version, and any sign text; `/build schematic download format:schem` returns a valid
   converted file; `/build schematic convert data_version:2586` reports the loss array.
2. **Phase 2** — submit a door, then resubmit the same door translated and rotated. The
   second submission's vote card must flag the first.
3. **Phase 3** — submit with `SQUID_SCHEMATIC_RENDER_ENABLED=1` and a pack configured; the
   vote card gains a rendered thumbnail after the background task completes. Then unset the
   pack and confirm submission still succeeds with one WARNING and no user-visible error.
4. **Phase 4** — run the three-door timing experiment *before* implementing; then
   `/build detect-lattice` on an expandable extender and check the reported unit cell against
   the known module size.

Resilience check at any phase: upload a deliberately corrupt `.litematic` and confirm the bot
logs a worker crash, respawns, and keeps serving other commands.

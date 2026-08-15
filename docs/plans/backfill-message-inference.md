# Backfill rework: grouped, contextual, multimodal build inference

> **Status.** Implemented on 2026-08-04. Focused unit, lint, formatting, type, and boundary
> checks pass; the credentialed Discord/OpenAI dry-run remains an operational follow-up.

## Implementation amendments

- `confidence` remains part of the model contract but is not copied into `Build.extra_info`.
  The domain's typed JSON shape has no durable confidence field, and persisting an undocumented
  key would make the inference change leak into the storage contract.
- ~~The message table is keyed by Discord message ID, so one source message cannot be tracked to
  every build when a bundle yields multiple builds.~~ **Resolved.** `build_source_messages` is
  many-to-many in both directions, so every primary message links to every build it produced and
  each keeps its own content instead of being concatenated into one row. Rerun idempotency is now
  `BuildService.list_ids_for_source_message`.
- Attachment mirroring is performed once per bundle and the resulting media is attached to each
  inferred build. The structured result deliberately contains source message IDs, not
  attachment IDs, and `Build` has no transient multi-message provenance field from which the
  ingestion layer could safely derive a narrower attachment set.

## Context

`scripts/populate_db_with_logs_historical_messages.py` walks `#build-logs` and `#record-logs`
history and asks a model to turn each message into a pending `Build`. It treats every message
as an isolated, text-only unit, which is wrong for how the channels are actually used:

- People split one build across several consecutive messages ("**Smallest 5x5**" / "0.8s" /
  "made by X"), and sometimes post two builds in four messages. One-message-in, one-build-out
  cannot express either case.
- People reply to a record with a screenshot and three words. Without the parent message the
  reply is unparseable; today it becomes a garbage pending build or is dropped.
- Even with no reply, the couple of messages before a post often establish what is being
  discussed.
- Attachments are ignored entirely. The live listener at least mirrors them to Catbox; the
  backfill does not, so backfilled builds carry no images, videos, or schematics at all — and
  the model never sees the one artefact that unambiguously shows the door type.
- The output contract is a hand-rolled `<target>key: value</target>` block scraped with a regex
  (`squid/builds/application/inference.py:187`). `OpenAITextGenerator.generate`
  (`squid/builds/infrastructure/text_generation.py:34`) calls `.beta.chat.completions.parse()`
  *without* a `response_format`, so the structured-output API is invoked but no schema is
  enforced. A single missing key silently discards the whole message.
- The prompt (`squid/builds/infrastructure/prompt.txt`) has three few-shot examples and a short
  field list. It carries none of the actual record taxonomy, so restriction and door-type names
  come back in forms the DB taxonomy then rejects into `extra_info["unknown_restrictions"]`.

Outcome: the backfill (and the live listener, which shares the inference service) reads a
message *bundle* — the author's consecutive run, its reply ancestry, and a short lookback
window — plus the images in it, and returns **zero or more** builds with real structured
output, guided by a prompt that knows the rules document.

## Decisions taken

| Question | Answer |
|---|---|
| Model | `gpt-5.6-luna`, `reasoning_effort="low"`, both configurable |
| Media | Mirror to Catbox now, behind a swappable port for a paid host later |
| Scope | Live listener and backfill share one context-assembly path |
| Rules in prompt | Hand-written condensed digest, not the full document |

---

## Step 1 — Land the rules document

Copy the uploaded file to `reference/Door_Rules.md` verbatim and commit it alone
(`docs: add squid records rules document`). It is the source the prompt digest is distilled
from, and the plan references it by section. It lives outside `docs/` on purpose: it is a
verbatim Google Docs export whose internal `#bookmark=id.…` links have no targets here, so
`zensical build --strict` rejects it as a site page.

## Step 2 — Structured output

**`squid/builds/application/inference.py`** — replace `_parse_output` with pydantic models.
Pydantic is already a dependency and the application layer may import it (the archrule at
`tests/architecture/test_boundaries.py` only bars sqlalchemy/discord/fastapi/nucleation).

```python
class InferredBuild(BaseModel):
    source_message_ids: list[int]      # which bundle messages this build came from
    build_category: Literal["Piston Door", "Entrance", "Piston Extender", "Utility"] | None
    component_restrictions: list[str]
    wiring_placement_restrictions: list[str]
    animated_restrictions: list[str]
    miscellaneous_restrictions: list[str]
    door_type: list[str]
    door_orientation: Literal["Normal", "Skydoor", "Trapdoor"] | None
    door_width / door_height / door_depth: int | None
    build_width / build_height / build_depth: int | None
    opening_time / closing_time: str | None     # free text, fed to parse_time_string
    creators: list[str]
    version_spec: str | None
    author_note: str | None
    confidence: Literal["high", "medium", "low"]

class InferenceResult(BaseModel):
    builds: list[InferredBuild]
```

Lists replace the old `", "`-joined strings, so `_split` and the `"none"/"null"/"unknown"`
sentinel handling both disappear. `_REQUIRED_FIELDS` (`inference.py:21`) disappears with them —
the schema enforces presence, and "no build here" is now an empty `builds` list rather than a
parse failure. Keep `_apply_taxonomy` and `_apply_fields` largely as-is; they now read typed
attributes instead of `dict[str, str | None]`.

**Port** (`inference.py:56`) becomes schema-generic so the adapter stays dumb:

```python
class StructuredGenerator(Protocol):
    async def generate[T: BaseModel](
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str,
        images: Sequence[InlineImage] = (),
        reasoning_effort: str | None = None,
    ) -> T | None: ...
```

**`squid/builds/infrastructure/text_generation.py`** — implement it on
`client.chat.completions.parse` (the non-beta path exists in the pinned `openai==2.46.0`),
passing `response_format=schema` and `reasoning_effort`. Images become
`{"type": "image_url", "image_url": {"url": "data:<content_type>;base64,<...>"}}` content parts
on the user message. Return `completion.choices[0].message.parsed`.

Add one degradation path: on `openai.BadRequestError` (provider without strict json_schema),
retry once with the schema appended to the system prompt as instructions and
`schema.model_validate_json` over the fence-stripped text. Log at warning so a misconfigured
provider is visible rather than silently slow.

## Step 3 — Contextual input model

**`squid/builds/application/inference.py`** — `BuildInferenceInput` (currently a flat
single-message record at `inference.py:44`) becomes a bundle:

```python
@dataclass(frozen=True, slots=True)
class ContextMessage:
    message_id: int
    author_name: str
    author_id: int
    content: str
    timestamp: str                                        # ISO-8601, shown to the model
    kind: Literal["primary", "reply_parent", "preceding"]
    attachment_summary: str                               # "2 images, 1 .litematic"

@dataclass(frozen=True, slots=True)
class InlineImage:
    data: bytes
    content_type: str
    source_message_id: int
    origin: Literal["attachment", "video_frame"]

@dataclass(frozen=True, slots=True)
class BuildInferenceInput:
    primary: tuple[ContextMessage, ...]     # the candidate group, chronological
    context: tuple[ContextMessage, ...]     # reply ancestry + lookback, never a build source
    images: tuple[InlineImage, ...]
    channel_id: int
    server_id: int | None
```

Add `BuildInferenceInput.from_single_message(...)` so existing tests and any simple caller keep
a one-liner.

`BuildInferenceService.infer` returns `list[Build]`. For each `InferredBuild` it resolves
`source_message_ids` against `primary` (dropping ids that aren't in the bundle — the model
does hallucinate ids), and sets the frozen provenance fields from the **earliest** resolved
message, with `original_message` set to the concatenated text of all resolved messages so the
stored record shows what was actually read. A build whose ids resolve to nothing is discarded
with a debug log.

## Step 4 — Bot-layer context assembly

New **`squid/bot/submission/message_context.py`**. It has to live in the bot layer because
`squid.*.application*` may not import discord.

- `group_messages(messages, *, window_seconds=300, max_messages=8)` — pure, typed against a
  narrow structural protocol (`id`, `author_id`, `created_at`, `reference_id`) so it is
  testable without discord objects. A run breaks when: the author changes, the gap to the
  previous message *by that author* exceeds `window_seconds`, the run hits `max_messages`, or
  the message replies to something outside the current run. Other authors' messages interleaved
  do **not** break a run — they land in that group's `context` as `preceding`.
- `resolve_reply_chain(message, *, max_depth=4, cache)` — walks `message.reference`, preferring
  `reference.resolved` and falling back to `channel.fetch_message`, with an id-keyed cache, a
  seen-set cycle guard, and tolerance for `DeletedReferencedMessage`. Nothing in the codebase
  touches `message.reference` today; this is all new.
- `collect_lookback(...)` — the N messages (default 3) immediately preceding the group,
  regardless of author. The backfill gets these free from a rolling `deque` over the history
  stream; the listener fetches them with `channel.history(before=..., limit=N)`.
- `collect_images(messages, *, max_images=6, max_bytes=4 MiB)` — reads image attachments via
  `classify_attachment` (`squid/bot/submission/attachments.py:37`) and, for videos, one frame
  via the existing `extract_first_frame` (`squid/bot/utils/web.py:113`, takes a URL, so it
  streams the Discord CDN directly). Oldest-first, capped by count and total bytes; no
  downscaling, since Pillow is not a dependency and adding it for this is not worth it.
- `assemble_bundle(...)` — ties the above into a `BuildInferenceInput`.

New **`squid/bot/submission/media.py`** — `MediaMirror` protocol (`upload(filename, data,
content_type) -> str`) with `CatboxMirror` wrapping the existing
`upload_to_catbox` (`squid/bot/utils/uploads.py:10`). Both the listener's inline block
(`submit.py:435-458`) and the backfill call the port, so moving to a paid host later is one new
class plus a config field rather than edits at two call sites.

## Step 5 — Shared ingestion path

Extract the listener body (`squid/bot/submission/submit.py:409-467`) into a reusable
`ingest_message_bundle(bundle_messages, context, services, *, model, mirror)` used by both
callers. It assembles the bundle, calls `infer`, and for **each** returned build: mirrors that
build's attachments, runs `_analyse_attachments` / `_note_schematic_duplicates`, submits, and
tracks every source message with `services.messages.track(..., purpose="build_original_message",
build_id=build.id)` — the purpose literal already exists
(`squid/messages/domain/models.py:8`), so no migration is needed, and this finally makes the
script's existing idempotency check at `scripts/...:79-81` actually fire on re-runs.

`infer_build_from_message` becomes: build a one-message group + reply chain + lookback, call
the shared function, post each build for voting. `recalc` (`submit.py:469`) is unaffected.

The two channel IDs are currently duplicated as literals at `submit.py:415-416` and
`scripts/...:24`; move them to one constant.

## Step 6 — Prompt rewrite

`squid/builds/infrastructure/prompt.txt` currently interpolates with `str.format`
(`inference.py:99`), so any literal brace in the file breaks it. Split it instead:

- `prompt.txt` becomes the **system prompt** with no placeholders at all.
- The service renders the bundle into the **user message** as `<messages>` XML — one
  `<message id author kind timestamp attachments>` element per entry, primaries and context
  clearly separated.

System prompt contents, in order:

1. Task: read a bundle of Discord messages from a records channel, return zero or more builds.
2. **Condensed rules digest** distilled from `reference/Door_Rules.md`: the title grammar
   (`<wiring placement> <animated> <size> <type> <orientation>`, §2.1), the four restriction
   axes with their real vocabularies (§2.1.4–2.1.7 — SEAMLESS tiers, FLUSH/DELUXE/TRAPDOOR,
   HIPSTER, EXPANDABLE/TILEABLE, SYMMETRICAL/SYNC/CLEAN, the `-less`/`ONLY` component
   restrictions, LOCATIONAL/DIRECTIONAL), door types (§2.1.2), record titles
   FIRST/FASTEST/SMALLEST/FASTEST SMALLEST/SMALLEST FASTEST (§4), volume = W×H×D of wiring
   (§4.2), and the terminology that changes parsing — door dimensions are the *frame*, build
   dimensions are the *wiring* (§5). Include the §7 note that `//` means "individually applied".
3. Per-field guidance, carried over from the current prompt where it is still right (the
   `miscellaneous_restrictions` note about "Smallest 2.1s 6x6" being a restriction is good and
   stays) and corrected where the schema changed.
4. **Bundle rules**: `primary` messages are the only build sources; `context` messages are for
   disambiguation only. A bundle may hold zero, one, or several builds — decide from content,
   not from message count. Attribute each build with `source_message_ids`. Ignore chatter,
   corrections-without-a-build, and "nice door" replies.
5. **Image rules**: images confirm door type, shape, and orientation; never infer dimensions or
   timings from an image alone; a screenshot attached to a reply describes the *reply's* claim.
6. Few-shot examples in the new JSON shape, at minimum: one multi-message single build, one
   four-message two-build bundle, one reply-with-screenshot-and-no-text that inherits its
   parent's category, and one bundle that yields `{"builds": []}`.

## Step 7 — Script rewrite

`scripts/populate_db_with_logs_historical_messages.py`:

- Stream `channel.history(oldest_first=True)` through the grouper with a rolling lookback
  deque; flush a group when it breaks. Keep `ImportSummary` and add `builds` (a group can now
  produce more than one) and `skipped_existing`.
- Bounded concurrency: `asyncio.Semaphore(--concurrency, default 4)` over group processing, so
  vision calls don't fan out unbounded. Channels stay concurrent.
- New flags: `--after` / `--before` (snowflake or ISO date), `--limit`, `--dry-run` (infer and
  log, write nothing), `--group-window`, `--group-max-messages`, `--no-images`,
  `--reasoning-effort`.
- `--model` keeps working; the default moves to config.

`squid/config.py`: add `chat_model: str = "gpt-5.6-luna"` and `reasoning_effort: str = "low"`
to `OpenAIConfig` (`config.py:87`), documented in `.env.example` as `SQUID_OPENAI_CHAT_MODEL`
and `SQUID_OPENAI_REASONING_EFFORT`. This removes both hardcoded literals
(`submit.py:430`, `scripts/...:25`). `gpt-5.6-luna` is the user's chosen default; because it is
config-driven, swapping it is an env change rather than a code change.

## Step 8 — Tests

- `tests/unit/bot/submission/test_message_context.py` (new): grouping across author change,
  gap, cap, and reply-out-of-group; reply-chain depth cap, cycle, and deleted parent; image
  caps.
- `tests/unit/builds/application/test_inference.py` (extend): the existing
  `FakeTextGenerator`/`FakeTaxonomy`/`FakeVersions` fakes (`test_inference.py:32-64`) get
  retargeted at the new port. New cases: two builds from one bundle, empty `builds`, a build
  whose `source_message_ids` are all bogus, context messages never becoming a build source,
  images reaching the generator.
- `tests/unit/builds/infrastructure/test_text_generation.py` (new): a fake OpenAI client
  asserting the multimodal content parts and the `BadRequestError` → lenient-JSON fallback.

## Verification

```bash
uv run pytest tests/unit/builds tests/unit/bot/submission tests/architecture --no-cov
uv run ruff format --check <changed> && uv run ruff check <changed>
uv run basedpyright squid/builds squid/bot/submission scripts
git diff --check
```

Then an end-to-end smoke run against real history, which is the only thing that proves the
grouping heuristics and the prompt:

```bash
uv run python scripts/populate_db_with_logs_historical_messages.py \
  --channel-id 726156829629087814 --limit 200 --dry-run
```

Read the dry-run log for: groups that merged two unrelated posts, groups that split one build,
replies whose parent was not resolved, and builds whose restriction names landed in
`extra_info["unknown_restrictions"]` (that last one is the direct measure of whether the rules
digest in the prompt is working). Iterate on `--group-window` and the prompt from that output
before any real write.

## Commits

1. `docs: add squid records rules document`
2. `builds: return structured inference results` (steps 2–3, tests)
3. `bot: assemble message bundles for inference` (step 4, tests)
4. `bot: share build ingestion between listener and backfill` (step 5)
5. `builds: rewrite the inference prompt around the rules document` (step 6)
6. `scripts: group, resume, and bound the historical backfill` (steps 7)

## Not in scope

Re-inferring builds already imported by earlier runs (the new tracking only guards future
runs); a separate schematic-analysis pass over already-backfilled builds; and populating the
`search_documents.embedding` path, which is a pre-existing dead path unrelated to this work.

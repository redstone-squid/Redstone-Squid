# 17 — Deferred text: the framework translates

## Problem

`squid_layouts` has no i18n support at all. It is marker-free by architecture rule
(`tests/architecture/test_boundaries.py:39`, because `babel.cfg` only scans `squid/**`),
and every user-visible string must arrive already translated. The whole cost lands on the
host, and the evidence is against that split:

- **The ceremony is uniform, and the defects live in repeating it.** 712
  `t(self.locale, _("…"))` calls across fourteen view modules. `self.locale` has to be
  threaded into every component, child component and modal constructor purely so those
  calls can reach it — 133 `locale` occurrences in `squid/bot/submission/ui/views.py`
  alone.
- **Marking and translating are two steps, so one can be missed silently.**
  `settings_view.py:551` marks a `Choice` description with `_()` and never wraps it in
  `t()`. It ships untranslated and reads as correct.
- **Chrome defaults to English and nothing enforces passing it.** `plan()`, `compose()`
  and `render_static()` all default to `DEFAULT_CHROME`; twelve of the thirteen
  `render_static` call sites in `squid/` pass no `chrome=`. Spill lines and page footers
  render in English on the bot's most-used reply paths (`ui.py:335,345,380`,
  `voting/rendering.py:72,131`, `starboard/render.py:50`). `chrome_for(locale)` being
  locale-shaped is what makes forgetting it possible.
- **Locale is baked at mount time.** `Chrome` is captured in `Mount.__init__`
  (`discord/mount.py:166-168`), so `/settings language` cannot retranslate the open panel.
  The comment at `settings_view.py:631` acknowledges half of it.
- **Interpolation skips escaping.** `translate()` does a raw `str.format`, so the 72 sites
  passing params never escape them. `md()` exists precisely to do this and has zero host
  callers. A build title containing `*` or `[` corrupts the surrounding Markdown today.

## Design

Split it the way plan 15 split delivery: **the framework owns resolution, the host owns
the catalogue.**

> A `Message` is text that carries its own msgid. It travels through the tree untranslated
> and is resolved once, at plan time, by the `Localization` the mount was given.

Locale stops being an author-time input and becomes a render-time one.

1. **`TextLike = str | ResolvedText | Message`.** `str` stays valid, so all 712 existing
   sites keep working and migration is incremental rather than a flag day.

2. **`Localization` is a locale tag plus its two catalogue lookups** — `gettext` and
   `ngettext` — which is exactly the surface `gettext.NullTranslations` already exposes,
   so the host builds one by handing over a catalog. The identity default (`NEUTRAL`)
   reproduces today's behaviour.

3. **Resolution routes through `md()`'s escaping.** `Message` params go through
   `_safe_value`, so interpolated content is Markdown-escaped and mentions neutralised.
   `raw_md()` is the existing opt-out. This closes the gap above as a side effect of the
   move, rather than as a separate sweep.

4. **Chrome stays host-supplied but stops being locale-shaped.** Its fields widen to
   `TextLike`; `localize_chrome()` resolves them to `str` at the entry points, so
   `solve.py`, `cursors.py`, `pagination.py` and `discord/navigation.py` keep seeing plain
   strings and are untouched. The host then holds **one module-level `CHROME` constant**
   instead of a per-locale factory, and there is nothing locale-shaped left to forget.

Chrome remains the host's rather than moving into a package-owned catalogue: wording has
to stay overridable (`diagnostics_view.py:52-66` overrides three fields), and a second
gettext domain would mean two extraction runs and translators working in two places.

## Stages

### 1. Deferred text in the package

`text.py` gains `Localization`/`NEUTRAL` and `Message`, next to `md()` — that is where
escaping and t-string handling already live, and `Message` reuses `_resolve_named`,
`_safe_value` and `_resolve_template` wholesale.

```python
@dataclass(frozen=True, slots=True)
class Localization:
    locale: str | None = None
    gettext: Callable[[str], str] = _identity
    ngettext: Callable[[str, str, int], str] | None = None

@dataclass(frozen=True, slots=True)
class Message:
    template: str
    params: Mapping[str, object] = _EMPTY
    dialect: TextDialect = TextDialect.DISCORD_MARKDOWN
    plural: str | None = None   # plural msgid; params["count"] selects
```

`resolve_text(value, localization)` takes the localization as a **required** second
argument rather than defaulting to `NEUTRAL`: a defaulted one would silently render
English, which is the failure mode this plan exists to remove. It has one caller outside
`adaptation.py`, so the cost is nil.

`chrome.py`: fields widen to `TextLike`, `localize_chrome()` is added, and
`LOCALIZATION_CONTEXT` joins `CHROME_CONTEXT`. The dead `see_attachment` field —
referenced nowhere in the repo — goes.

`planning/adaptation.py`: `_Context` (`:115`) carries the localization and
`lower_semantics()` takes it; the ~35 bare `resolve_text(x)` calls become
`resolve_text(x, context.localization)`. Four helpers need a context parameter they lack
today: `_field_entry:255`, `_picker:735`, `_unbound_button:752`, `_button:770`.

`planning/planner.py`, `planning/solve.py:745`, `discord/compose.py:40,75`: a
`localization: Localization = NEUTRAL` keyword, with `chrome` localized immediately on
entry — before `PageBroker` and before the cache key, so everything downstream still sees
a `str`-valued `Chrome`.

**`localization.locale` joins `_plan_cache_key` (`:580`).** Author `Message`s are resolved
during lowering, which happens *after* the cache lookup, so two locales would otherwise
collide on one entry. This is the one place where the deferral is not transparent.

### 2. Live locale on the mount

`Mount` takes a `localization`, keeps the unresolved `chrome` beside the localized one,
and provides both context keys to the runtime. `Mount.localize(localization)` swaps it,
re-derives the localized chrome and `self.nav` (`default_nav` is computed once at `:168`
today), and invalidates. `ComponentRuntime` needs its context mapping to be updatable —
a narrow setter in `runtime/owner.py`, not a rebuilt runtime.

Two consequences fall out:

- `ActionEvent.locale` (`mount.py:492-497`) starts carrying the *negotiated* locale
  instead of the raw `interaction.locale`, which ignores the guild override. It has no
  readers today, so it is currently a trap rather than a feature.
- `notice()` (`actions.py:41,68`) accepts `TextLike`, resolved with the mount's
  localization — as do `session_ended` and `not_yours` (`mount.py:436,439`).

### 3. The `L` marker

```python
def L(message: str | Template, /, **params: object) -> ui.Message:
    """Mark and defer a translatable string: `L(t"Page {page} of {pages}")`."""
```

One letter on purpose, mirroring `_`. It lives in `squid/bot/ui.py` because only
`squid.bot*` may import `squid_layouts`.

A `Template` has already substituted its values by the time `L` sees it, so `L`
reconstructs the msgid from the static parts and the interpolation *expressions*, taking
the values as params:

```python
t"Page {page} of {pages}"  ->  Message("Page {page} of {pages}", {"page": 3, "pages": 7})
```

`Interpolation.expression` carries the source text and `.value` the object. Every
interpolation must satisfy `expression.isidentifier()`: `t"{page + 1}"` yields expression
`'page + 1'`, which is not a usable placeholder name and must raise. The `str` form
(`L("Page {page}", page=page)`) stays for computed values.

### 4. The Babel extractor

Babel 2.18's token-based extractor **silently yields a `None` msgid** for `L(t"…")` — it
does not error, the string just vanishes from the catalogue. Supporting t-strings
therefore means owning an extractor. New `squid/core/extract.py`:

```python
def extract_squid(fileobj, keywords, comment_tags, options):
    data = fileobj.read()
    yield from extract_python(io.BytesIO(data), keywords, comment_tags, options)
    for node in ast.walk(ast.parse(data.decode("utf-8"))):
        # Call to Name 'L' whose first arg is an ast.TemplateStr:
        # msgid = "".join(Constant.value | "{" + Interpolation.str + "}")
        yield (node.lineno, "L", msgid, [])
```

Delegate-then-augment, because Babel uses only the **first** mapping whose pattern matches
a file — two extractors cannot both run over `squid/**`. The AST carries everything
needed: `ast.TemplateStr` holds `Constant` and `Interpolation` nodes, and
`Interpolation.str` is the source expression. (The AST spelling is `.str`; the runtime
spelling is `.expression`. They are not the same attribute.)

`babel.cfg` becomes:

```
[squid.core.extract.extract_squid: squid/**.py]
```

**Dots, not `module:function`.** Babel splits the section header on the first colon, so
`[squid.core.extract:extract_squid: …]` parses the *pattern* as `squid.core.extract`,
matches nothing, and extracts silently — the same class of trap as the `None` msgid.
Verified working end to end with the dotted spelling.

`justfile:140` gains `-k L` for the plain-string form. pybabel adds to the default keyword
set, so `_()` and `locale_str()` extraction are unaffected.

### 5. Host glue

- `catalog_for(locale)` in `squid/core/i18n.py`, wrapping the existing `_catalog` (`:33`)
  so the bot does not reach for a private name.
- `localization_for(locale)` in `squid/bot/ui.py`, built straight from that catalog.
- `chrome_for(locale)` (`:73-86`) becomes a module-level `CHROME` constant of `L(...)`
  values. `create_mount`/`send_component`/`render_item` pass it plus the localization.
- `card_layout`, `text_layout`, `link_layout`, `error_layout`, `info_layout`,
  `warning_layout` and `help_layout` gain `locale: str | None = None` and pass chrome and
  localization through. This is what fixes the silent-English paths. Their text parameters
  widen to `ui.TextLike`, and `link_layout`'s hardcoded `label="Open link"` (`:370`)
  becomes `L(t"Open link")`.
- `PagedList._page_footer` (`:282`) collapses to a single `L(...)`.

### 6. Guard tests

Two AST-based architecture tests:

- **Catalogue completeness.** Walk `squid/**` for `L(...)` calls, derive each msgid the
  way the extractor does, assert it is in `locales/squid.pot`. Catches a t-string the
  extractor missed *and* an ordinary `L()` that never reached the catalogue — a gap that
  exists today too, since nothing asserts `just i18n-extract` is up to date. The
  derivation is shared with `squid/core/extract.py` so test and tool cannot drift.
- **Render containment.** Forbid `squid_layouts.discord.render_static`/`compose` outside
  `squid/bot/ui.py`, so the host wrappers stay the only door and the twelve chrome-less
  call sites cannot come back.

`test_layouts_package_carries_no_translation_markers` (`:39`) is untouched and must still
pass — no `_()` enters the package.

### 7. Pilot migration

Three modules, each proving a different part:

- **`diagnostics_view.py`** — the only per-component `Chrome` override (`:52-66`), with a
  nested `t()` inside a `page_footer` lambda. Proves the override path survives becoming
  locale-free.
- **`settings_view.py`, `SettingsPanel` only** (`:344` onward; the legacy
  `SettingsPanelView` at `:74` is left alone). Proves live switching — `set_locale`
  (`:628`) calls `mount.localize(...)` and the whole panel including chrome retranslates —
  and fixes `:551` by construction.
- **`claims_view.py`, `ClaimReviewComponent` only** (`:212`) — the per-node
  `Paginate(footer=...)` override path.

Shape: `t(self.locale, _("x"))` → `L(t"x")`; `t(self.locale, _("x {y}"), y=v)` →
`L(t"x {v}")` where `v` is a bare name, otherwise bind a local first or use the `str`
form. Then delete the `self.locale` plumbing nothing else reads.

**Escaping hazard, per site:** params are now escaped, so any param carrying intentional
Markdown needs `ui.raw_md()` — notably channel and role mentions like `f"<#{channel_id}>"`
(`settings_view.py:306`), which would otherwise render as literal text.

Because `L(t"x")` derives the same msgid as `_("x")`, the pilot produces **no churn in
`locales/squid.pot` or the `.po` files**, except where an interpolation renames a
placeholder — which the completeness test surfaces.

## Verification

1. `uv run pytest packages/squid-layouts/tests -q --no-cov`. New cases: a `Message`
   resolves through a stub `Localization`; params are escaped and `raw_md` passes through;
   one tree under two locales takes two cache entries; `Mount.localize` re-renders with new
   chrome.
2. `uv run pytest tests/architecture tests/unit/bot tests/unit/core -q --no-cov` — the
   marker-free rule plus both guard tests.
3. `extract_squid` unit-tested directly on a fixture holding `L(t"…")`, `L("…")`, `_("…")`
   and `L(t"{a + 1}")`: the first three extract, the last is rejected.
4. `just i18n-extract && git diff --stat locales/` — expect an empty diff, confirming the
   extractor and `-k L` pick up exactly what `_()` used to.
5. `just typecheck`, compared against a pre-change run; the `TextLike` widening surfaces
   any position that assumed `str`.
6. `just i18n-compile && uv run pytest tests/unit/core/test_i18n_catalogs.py -q --no-cov`.
7. By hand: `/settings`, switch the language picker, confirm the panel body *and* the
   Previous/Next/Close buttons retranslate without reopening. Then a diagnostics report,
   for the overridden chrome and paginated footer.

## Not in scope

- The other eleven view modules (~650 call sites). They keep working on `str`; convert
  opportunistically.
- Locale-aware number formatting inside the package (`adaptation.py:232`'s `{ratio:.0%}`,
  the list markers at `:199`). Recorded in `90-deferred.md`.
- A package-owned catalogue, for the reasons under Design.

## Correction

Two exploration passes flagged `squid/core/i18n.py:49`
(`except UnknownLocaleError, ValueError:`) as a syntax error. It is not: PEP 758 makes
unparenthesized multi-type `except` valid on the 3.14 target. No change needed.

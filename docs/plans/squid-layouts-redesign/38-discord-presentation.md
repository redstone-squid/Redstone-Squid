# 38 — Complete Discord presentations

## Problem

`squid-layouts` cannot say what it is about to put on a Discord message.

`Destination` receives `(LayoutView, files)`. `EditHandle.write` receives a view plus
attachments. Content and embeds — the other half of every Discord message — are handled by a
private guess in `delivery._legacy_fields`, which reads a `discord.Message` that is very often
`None` and returns `{"content": None, "embed": None}` when it cannot prove otherwise.

That is incomplete on its own terms, before any second target exists:

- the payload Squid owns is not a value, so it cannot be staged, audited, logged, diffed, or put
  in a durable snapshot;
- assets travel as a parallel positional parameter whose ordering every caller must remember
  (`destination(view, files)`), and `mount._attachment_files` is private to the mount, so
  sessionless composition has no asset story at all;
- clearing legacy fields is inferred per-handle rather than stated once, so the three handle
  classes each re-derive it and a handle with no readable message clears defensively;
- nothing anywhere records which message mode a live handle is addressing.

Discord's Components V2 flag is set implicitly by discord.py — `http.handle_message_parameters`
turns it on whenever `view.has_components_v2()` — and discord.py never checks that the rest of
the payload agrees. A library that owns delivery should hold the whole surface as one value.

## A. One complete replacement payload

```python
@dataclass(frozen=True, slots=True)
class DiscordPresentation:
    mode: DiscordMode
    content: str | None
    embeds: tuple[discord.Embed, ...]
    view: discord.ui.View | discord.ui.LayoutView | None
    assets: tuple[Asset, ...]
```

`DiscordMode` is `CLASSIC` or `COMPONENTS_V2`. A presentation describes the whole outgoing
message surface Squid owns; absent content and embeds are explicit clears, not omitted kwargs.
It exposes `files()` to materialize fresh file wrappers on each call, and a package-private
conversion to discord.py send/edit kwargs.

Construction validates coherence, because the invalid combinations are exactly the ones Discord
rejects with a 400 that names nothing useful:

- `COMPONENTS_V2` with a non-`None` `content` or a non-empty `embeds`;
- `COMPONENTS_V2` whose view is not a `LayoutView`, or `CLASSIC` whose view is one;
- a `CLASSIC` view that nevertheless reports `has_components_v2()`.

That last check is not redundant. `ActionRow._is_v2()` returns `True` deliberately, so a view
that would serialize to a perfectly legal classic payload can still carry the flag.

`Composition` gains `presentation`. `Composition.view` stays as a property so the nine in-tree
consumers and every V2 caller keep working unchanged.

## B. Delivery operates on presentations

`Destination.__call__(presentation)` and `EditHandle.write(presentation)` replace their
view-plus-files signatures, so delivery atomicity covers every rendered field rather than one of
them. Assets stop travelling beside the payload.

**This is a breaking change for hosts.** `Destination` is a public protocol; `reply_to`,
`respond_to`, `_ChannelMessageHandle`, `_WebhookMessageHandle`, `_OriginalResponseHandle` and the
doubles in `discord/testing.py` all change with it. There is no shim: a `Destination` that still
takes a view would silently drop content and embeds, which is worse than not compiling.

Every destination keeps owning transport policy — ephemerality, waiting, allowed mentions, DM
fallback, and host-supplied files. It merges host files with `presentation.files()` and rejects
attachment overflow before calling Discord. `AllowedMentions.none()` remains the default.

## C. Message-mode transitions are explicit

The delivery adapter knows the previous mode when a message object is available:

- classic → classic edits content, embeds, view, and attachments as one payload;
- classic → V2 clears legacy content and embeds and installs the `LayoutView`;
- V2 → V2 edits the layout normally;
- V2 → classic raises `DiscordModeError` before HTTP, because Discord's Components V2 flag is not
  reversible on a sent message.

`_legacy_fields` disappears into that matrix. Where no source message is readable — an
interaction response whose message was never fetched — the standing handle records the mode it
delivered, so the second edit is not a guess. Durable snapshots store the mode beside the
locator, and recovery restores it.

`DiscordModeError` subclasses `LayoutError`, following plan 35 §D.

## D. Assets become part of the payload

`mount._attachment_files` and `mount._linked_file_assets` move out of the mount and hang off the
presentation. This is the same move plan 35 §E.4 asks for; whichever plan lands first owns it and
the other's bullet is struck rather than done twice.

The result is that a sessionless `compose()` can carry a file, which today it cannot, and that a
`Fragment.files()` has something to be built on.

## Non-goals

- Producing classic presentations. This plan lands with `COMPONENTS_V2` the only mode any Squid
  code path constructs. `CLASSIC` exists so the type is honest and so the transition matrix can
  be written and tested once; [36](36-classic-discord-target.md) is what fills it.
- Choosing a mode automatically. The author picks a target.
- Owning transport policy. Destinations keep it.

## Implementation sequence

1. `discord: hold the whole outgoing message as a value` — `DiscordPresentation`, `DiscordMode`,
   coherence validation, kwargs conversion, `Composition.presentation`.
2. `discord: deliver complete presentations` — destinations, edit handles, mount, and the
   transition matrix replacing `_legacy_fields`.
3. `discord: carry assets on the presentation` — asset extraction out of `mount.py`.
4. `durability: record the delivered message mode` — snapshot field and recovery.

## Verification

- A presentation rejects every incoherent combination at construction, including a `CLASSIC` view
  that reports `has_components_v2()`.
- `files()` is repeatable and returns fresh `discord.File` objects each call.
- Delivery tests cover classic → classic, classic → V2 with explicit clears, V2 → V2, and
  preflight rejection of V2 → classic, across channel, `@original`, and webhook handles.
- A handle delivered without a readable message still refuses V2 → classic on its second write.
- The mode round-trips through a durable snapshot and recovery.
- Run the focused Discord delivery, mount, composition, and durability suites with `--no-cov`,
  then `just typecheck`, changed-file formatting/linting, architecture tests, and
  `git diff --check`. The delivery blast radius warrants the full package suite locally.

## Status

Proposed 2026-08-22, split out of [36](36-classic-discord-target.md) so it can land on its own
merits. 36 depends on this plan; this plan depends on nothing in 36.

# PR 200 review follow-up plan

## Scope and audit baseline

Address all 16 inline comments by `Glinte` on
[PR 200](https://github.com/redstone-squid/Redstone-Squid/pull/200),
“Schematics phase 0 stack 16”. The review was inspected on 2026-11-13 against
published head `e899338aab396f7b951511130dec2350fe391e09` and local commit
`f4263a3a7`. All 16 threads were unresolved. The review summary was empty;
the general conversation contained no additional comments by `Glinte`.

The local branch is `schematics-phase-0-stack-23`; PR 200 publishes
`schematics-phase-0-stack-16`. A local fix does not establish that the published
PR contains it. Recheck both heads before implementation or reporting completion.
An outdated GitHub anchor also does not establish that its concern is resolved.

Paths below are relative to `packages/squid-ui-discord/src/squid_ui_discord/`.
The former `DiscordUI` is now `Scope`, `DiscordRequest` is now `Request`, and
`ext/cog.py` has moved to `cog.py`.

## Comment inventory

| # | Comment and original location | Audit result |
| --- | --- | --- |
| 1 | [“should be generic to the ClientT too”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889164939), `facade.py:44` | Outstanding: `Scope` retains only `OwnerT`; its runtime uses `discord.Client`. |
| 2 | [“why is request untyped”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889170843), `facade.py:206` | Addressed in both snapshots with `request: Request[Any]`. Verify owner/client precision during P1. |
| 3 | [“some of these are needed, but too many new types for no good reason”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889172055), `contracts.py:29` | Outstanding: the alias layers remain substantially intact. |
| 4 | [“I wonder if this is really needed”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889172568), `facade.py:82` | Outstanding design decision: `_policy` still discovers `__response_spec__` on the content class. |
| 5 | [“we gotta eliminate circular imports that make this needed”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889172982), `facade.py:90` | Outstanding: the facade import moved, but request resolution still imports the runtime locally to break cycles. |
| 6 | [“match statement?”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889173466), `facade.py:98` | Outstanding: `render` still uses an `isinstance` ladder. |
| 7 | [“why is destination untyped”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889175526), `facade.py:237` | Fixed locally with `MessageDestination`; still untyped in the PR snapshot. |
| 8 | [“untranslated?”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889177167), `facade.py:264` | Needs a disposition: the anchored string is a `TypeError` diagnostic about session delivery without a source. |
| 9 | [“why is a cast needed here”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889179227), `facade.py:307` | Outstanding: the sentinel-unwrapping cast moved into `ResponseSpec.resolve`. |
| 10 | [“so many casts...”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889182612), `facade.py:325` | Partially restructured: policy resolution still casts access, and facade/request casts remain. |
| 11 | [“too many getattr, we should just discriminate the types of the source”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889185667), `request.py:58` | Outstanding: source normalization and accessors still use `getattr` and casts. |
| 12 | [“why is a cast needed here”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889202184), `request.py:312` | Original audience-selection code was removed in both snapshots. Verify the replacement preserves narrowing and deferral behavior. |
| 13 | [“can we keep a stronger type for dict[str, object]? ParamSpec? Concatenate? idk”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889205368), `request.py:338` | Outstanding: `form` callbacks still receive `dict[str, object]`. |
| 14 | [“we can just copy upstream and then add kwargs”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889219467), `ext/cog.py:23` | Outstanding: `__init_subclass__` still accepts `**kwargs: object`. |
| 15 | [“idk, can we do something better? this is not convenient for devs”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889225280), `ext/cog.py:25` | Outstanding: overriding `cog_load`/`cog_unload` is forbidden; developers must use `ui_load`/`ui_unload`. |
| 16 | [“urm, detecting a public attrbute isnt a good idea”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889247527), `ext/cog.py:34` | Fixed locally through `DiscordUIRuntime.of(bot)`; the PR still inspects `bot.ui`. |

## Implementation work

### P1 Preserve types through the facade and request boundary

Comments: 1–3, 7, 9–12.

- [ ] Trace runtime, scope, request, and command callback types. Preserve the concrete
  client type alongside the owner type through construction and public accessors.
  Avoid replacing lost type information with `Any` at the next boundary.
- [ ] Audit alias consumers before consolidating `InteractionSource`, `CommandSource`,
  `RequestSource`, and `ResponseSource`. Keep aliases that express a useful contract;
  remove redundant names and update exports, callers, and documentation together.
- [ ] Retain the existing request and destination annotations. Tighten their type
  parameters where the enclosing scope establishes the relationship.
- [ ] Narrow native interaction, command context, and message sources by their
  actual types. Inspect structural `Replyable` support and its callers before
  changing accepted inputs; isolate any necessary adapter boundary.
- [ ] Review sentinel and access-policy narrowing in `ResponseSpec.resolve`.
  Remove redundant casts and justify genuinely necessary ones at the boundary
  that establishes their invariant. Moving casts does not close the concern.
- [ ] Verify concrete client/owner inference, hybrid contexts, bare messages,
  private delivery, and deferred response audience compatibility.

### P2 Remove import cycles and clarify response policy discovery

Comments: 4–6.

- [ ] Trace the dependency cycle between runtime, scope, and request resolution.
  Move ownership lookup or request construction to its natural owner so runtime
  imports can be module-level. Do not add forwarding modules merely to hide cycles.
- [ ] Inspect screen declarations and policy precedence. Decide whether
  `__response_spec__` discovery is needed or can be replaced by an explicit typed
  contract. Preserve installation defaults, screen defaults, and per-call overrides.
  Record the rationale if class-level declarations remain useful.
- [ ] Express static render dispatch with `match`, preserving `LayoutView` before
  `View`, native payload passthrough, and component/document rendering behavior.
- [ ] Verify imports in a fresh interpreter and focused rendering/policy tests.

### P3 Decide diagnostic localization and form value typing

Comments: 8, 13.

- [ ] Trace the session-delivery `TypeError` through error handling. If it is only a
  programmer diagnostic, retain it and record that rationale. If users can see it,
  translate the user-facing error at the presentation boundary.
- [ ] Inspect `FormSpec`, schema evaluation, modal construction, and callback callers.
  Choose a typed result contract that relates declared fields to submitted values.
  Distinguish raw control values from validated values and preserve validation ownership.
  `ParamSpec` alone does not describe the keys and values in a result dictionary.
- [ ] Verify callback type inference and runtime submission/validation behavior,
  including optional fields and invalid input, for the chosen contract.

### P4 Improve cog integration without weakening cleanup

Comments: 14–16.

- [ ] Inspect the pinned discord.py implementation and typing for cog class keywords.
  Reuse or mirror its supported keyword contract, adding only library-owned options.
- [ ] Design a lifecycle integration that allows familiar cog hooks while ensuring
  scope closure and context-menu cleanup. Evaluate composition or integration at
  registration/removal before choosing an interception mechanism.
- [ ] Migrate existing cog subclasses consistently. Preserve cleanup when loading
  fails, unloading raises, or subclasses inherit hooks.
- [ ] Retain `DiscordUIRuntime.of(bot)` and verify runtime lookup does not depend on
  an arbitrary public `ui` attribute.
- [ ] Run focused lifecycle, rollback, context-menu, and runtime-lookup tests.

## Execution and completion

Start with P1's type boundaries, then P2's dependency and policy changes. P3 and P4
can follow as separate reviewable changes. Commit independently valid pieces as
they are completed. Preserve unrelated work already present in the checkout.

For implementation changes, run affected tests with `--no-cov` during development
and after the final edit. Format and lint affected files, confirm installed commit
hooks, run `just typecheck` once for the whole project, and run `git diff --check`.
Leave the full suite to CI unless the resulting scope or uncertainty warrants it.

Before declaring the review addressed:

- [ ] Give every inventory item an implementation commit or a concrete, justified
  disposition, including items already addressed at the audit baseline.
- [ ] Recheck the published PR head separately from the implementation branch.
  Identify which fixes still need propagation through the stack; do not claim that
  local completion means PR 200 has been updated.
- [ ] Report remaining GitHub thread state separately from code status. Posting
  replies or resolving threads requires an explicit request to do so.

This plan does not implement changes or update GitHub threads.

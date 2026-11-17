# PR 200 review follow-up

## Completion audit — 2026-11-17

All 16 inline comments by `Glinte` on
[PR 200](https://github.com/redstone-squid/Redstone-Squid/pull/200) have a local
implementation or the explicit disposition below. The original audit inspected
published head `e899338aab396f7b951511130dec2350fe391e09` on 2026-11-13.
The review summary was empty and the conversation added no requests.

GitHub was rechecked on 2026-11-17: PR 200 is closed without merging and retains
that head on `schematics-phase-0-stack-16`. All 16 threads remain unresolved
(seven anchors are outdated). Follow-ups are committed locally on `master`;
this audit does not change the closed PR, post replies, resolve threads, or push.
The implementation must be published from the current branch independently of
the closed stack snapshot.

Paths below refer to `packages/squid-ui-discord/src/squid_ui/ext/discord/`.
The former DiscordUI/DiscordRequest are now Scope/Request, and ext/cog.py is cog.py.

## P1 Typed requests and source normalization

Scope/Request propagate concrete client and owner types through runtime construction,
command bindings, source factories, and public accessors. Typing fixtures pin bot,
owner, native context, interaction, and component-event inference. Destinations
retain MessageDestination; no untyped presentation boundary was reintroduced.

`b7bfe72bd` consolidates source contracts into RequestSource, accepting native
Interaction, Context, Message, and structural Replyable adapters. Native matching
populates one immutable source record. Accessors and delivery use that record;
structural reflection is confined to adapter normalization and memo storage.
Interaction casts express the already established same-runtime client type;
structural casts are confined to the adapter's native-conformance boundary.
Useful content, destination, and localization-resolver contracts remain distinct.
The bot's localization adapter and test support use the consolidated source type
(`866beac72`).

`1f7f88355` removes both policy sentinel casts with explicit narrowing. Existing
ledger tests retain private replies, managed deferral, external acknowledgement,
hybrid contexts, and audience mismatch checks.

## P2 Imports, policy, and static dispatch

Lifetime now owns the weak installation registry and source lookup. Request factories
call that owner directly; they no longer import runtime construction locally.
Annotation-only MessageRoot/Session/SessionSpec dependencies remove the reverse
runtime dependency, allowing root request resolution at module scope. A fresh
interpreter imports the package successfully.

Keep compiled Screen defaults behind the typed Screen contract. They combine
inherited audience/access and implicit session names before installation defaults
and per-call overrides are resolved. Removing them would move that work to every
caller. Policy precedence is covered by screen/facade tests. Static rendering uses
match, with LayoutView before View and native payloads passed through.

## P3 Diagnostics and validated forms

Keep the session-without-source TypeError as an English developer diagnostic: it
reports an invalid API call before delivery, rather than a reader-correctable error.
The bot's unexpected-error presenter records the exception and sends a translated
generic notice with a reference, without exposing the diagnostic text.

Request.form now accepts a typed Form instance. Its schema parses fields and runs
cross-field validation before on_submit receives the same typed instance. Required
and optional descriptors retain their value types. A separate required on_invalid
handler receives FormResult, including attempted input and errors, so application
presentation owns correction/retry. Invalid input never reaches on_submit.
Runtime tests cover valid/invalid integers and omitted optional text; typing fixtures
pin int and str | None access.

Dynamic FormSpec callbacks still receive raw control dictionaries and own evaluation;
native Modal/ModalSpec handling remains native. ParamSpec describes callable argument
lists, not the runtime keys of a dynamic schema, so it cannot strengthen that raw
mapping. Static declarations use Form descriptors for this relationship instead.

## P4 Cog lifecycle and native keywords

Inspected the installed discord.py CogMeta implementation and typing. The library
has no __init_subclass__ override or additional class keywords; native CogMeta owns
name, description, command_attrs, and group keyword behavior directly. Copying its
implementation would introduce a second keyword contract without adding behavior.

Keep per-instance wrappers around ordinary cog_load/cog_unload hooks. Native bot
registration invokes those hooks; subclass authors use familiar overrides and
super() without opting into a separate registration API. A separate bot integration
or copying private injection/removal code would require hosts to coordinate the
same cleanup manually. Wrapping once per instance avoids repeating setup through
an inheritance chain. Menus register before loading; failed load and failing unload
both close scopes and remove menus in finally blocks. Focused tests cover rollback,
context menus, inherited hooks with/without super(), and runtime lookup without ui.

## Validation

Fresh package import, concrete client/owner typing, request, runtime, command/cog,
facade response/scope, and screen tests pass. Commit hooks format/lint changed files.
The final full local suite passed: 8,982 tests, with 20 opt-in cases skipped.
Whole-project Pyrefly reports zero errors; `git diff --check` passes.

## Comment inventory

| # | Comment and original location | Audit result |
| --- | --- | --- |
| 1 | [“should be generic to the ClientT too”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889164939), `facade.py:44` | Satisfied: Scope and Request preserve both owner and concrete client types; typing fixtures pin their accessors. |
| 2 | [“why is request untyped”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889170843), `facade.py:206` | Satisfied: facade request annotations retain the enclosing owner/client relationship. |
| 3 | [“some of these are needed, but too many new types for no good reason”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889172055), `contracts.py:29` | Satisfied in `b7bfe72bd`: one RequestSource contract replaces redundant source aliases; content/destination contracts remain useful. |
| 4 | [“I wonder if this is really needed”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889172568), `facade.py:82` | Disposition: typed Screen policy discovery preserves inherited defaults and session naming; see P2. |
| 5 | [“we gotta eliminate circular imports that make this needed”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889172982), `facade.py:90` | Satisfied in `b7bfe72bd`: Lifetime owns lookup; request/root imports are module-level, rendering dependencies annotation-only. |
| 6 | [“match statement?”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889173466), `facade.py:98` | Satisfied in `1f7f88355`: match dispatch preserves LayoutView priority and native passthrough. |
| 7 | [“why is destination untyped”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889175526), `facade.py:237` | Satisfied: MessageDestination annotates delivery; concrete target/client inference is tested. |
| 8 | [“untranslated?”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889177167), `facade.py:264` | Disposition: retain the developer-only TypeError; public errors use the translated generic notice, see P3. |
| 9 | [“why is a cast needed here”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889179227), `facade.py:307` | Satisfied in `1f7f88355`: explicit sentinel narrowing removes resolver casts. |
| 10 | [“so many casts...”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889182612), `facade.py:325` | Satisfied: policy/access narrowing removes casts; remaining adapter casts have their source/client invariant at the boundary. |
| 11 | [“too many getattr, we should just discriminate the types of the source”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889185667), `request.py:58` | Satisfied in `b7bfe72bd`: native source discrimination fills one normalized record; reflection is confined to structural adapters. |
| 12 | [“why is a cast needed here”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889202184), `request.py:312` | Satisfied: replacement request ledger narrows native interaction/context delivery and tests deferral audience compatibility. |
| 13 | [“can we keep a stronger type for dict[str, object]? ParamSpec? Concatenate? idk”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889205368), `request.py:338` | Satisfied in `b7bfe72bd`: typed Form callbacks receive validated instances; invalid results retain attempted input. |
| 14 | [“we can just copy upstream and then add kwargs”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889219467), `ext/cog.py:23` | Disposition: no __init_subclass__ override remains; native CogMeta owns its complete keyword contract. |
| 15 | [“idk, can we do something better? this is not convenient for devs”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889225280), `ext/cog.py:25` | Satisfied: ordinary cog_load/cog_unload overrides retain cleanup, inherited hooks, and rollback tests. |
| 16 | [“urm, detecting a public attrbute isnt a good idea”](https://github.com/redstone-squid/Redstone-Squid/pull/200#discussion_r3889247527), `ext/cog.py:34` | Satisfied: installation lookup uses Lifetime through DiscordUIRuntime.of, independent of bot.ui. |

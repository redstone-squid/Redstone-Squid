# 51 — Screens: the per-open half of a session

## Problem

[43](43-mount-defaults.md) gave the host-wide half of an open a value: `MountDefaults` holds
what every mount in a process shares, `SessionRegistry` holds one, and `open` accepts a bare
`Component`. The per-*screen* half never got the same treatment, so it is still duplicated by
convention.

`squid/bot/consent.py:520-548` and `squid/bot/submission/consent_banner.py:80-105` open the
same logical screen. Both build `SessionKey.user("consent", user_id)`, both pass
`SessionPolicy(collision=Reject())`, both derive `Owner(user_id)` and `actor_id=user_id` from
the same person, and both set `timeout=120`. Nothing but a comment connects them:

```python
# squid/bot/submission/consent_banner.py:84-86
# The same key `prompt_for_consent` uses, so the banner button and the account panel share
# one prompt between them rather than each opening their own.
```

That comment is load-bearing. If either site changes its key, the two prompts silently stop
colliding and a user gets two consent screens — the exact failure the shared key exists to
prevent, with no test that can see it, because both sites are individually correct.

Three facts are derived from one person at each site — the access policy, the session scope,
and the actor id — and each is re-derived by hand. `open_personal` (`discord/sessions.py:741`)
already noticed this for the common ephemeral case and fixed it for exactly one shape.

## Decision

**A `Screen` is a frozen value holding the per-open half, as `MountDefaults` holds the
per-host half.** Nothing new is expressible. Values, not class bodies: 43's rejection of
CascadeUI's class-body policy surface is unchanged and this does not reopen it, because a
`Screen` is a value a host constructs and may construct twice, not an attribute of a portable
component.

## Design

### 1. `Opener` — who an open is for

```python
@dataclass(frozen=True, slots=True)
class Opener:
    user_id: int
    guild_id: int | None = None

    @classmethod
    def of(cls, interaction: discord.Interaction) -> Opener:
        return cls(interaction.user.id, interaction.guild_id)
```

One value, three derivations. This is the whole point of the plan: `access`, `SessionKey`
and `actor_id` stop being three independent hand-written expressions that happen to name the
same person.

`Opener` is deliberately not `sl.Actor` (`actions.py:56`). That is portable frontend identity
(`id: str`, `display_name`) travelling on an `ActionEvent`; this is a Discord snowflake pair
used to build a scope. Merging them would put `guild_id` on a portable type.

`of()` is a convenience, not the only constructor: `squid/bot/consent.py` opens from a
`ConsentTarget` that may be a `Context`, and constructs `Opener(user_id)` directly.

### 2. `Scope` and `Screen`

```python
class Scope(StrEnum):
    USER = "user"
    GUILD = "guild"
    USER_GUILD = "user_guild"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class Screen:
    name: str
    scope: Scope = Scope.USER
    policy: SessionPolicy = DEFAULT_SESSION_POLICY
    access: Callable[[Opener], AccessPolicy] = _owner
    options: MountOptions = ...

    def key(self, opener: Opener) -> SessionKey: ...

    async def open(
        self,
        sessions: SessionRegistry,
        component: Component,
        destination: Destination,
        *,
        opener: Opener,
        parent: Mount | None = None,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult: ...
```

`Scope` selects the `SessionKey` constructor rather than reimplementing it;
`Scope.CUSTOM` is deliberately absent, because a custom scope needs a value the screen cannot
derive from an `Opener`, and such a call site should use `SessionRegistry.open` directly.

`key()` is public because the consent sites need the key itself, not only an open: one of
them looks up a parent session by it.

### 3. Four rules

1. **`access` is a function of the opener, not a default.** 43's "Considered, not done"
   rejects a registry-level default `access` because it would reintroduce the implicit-owner
   inference [34](34-safe-session-runtime.md) §A.1 removed. This does not: the derivation is
   named at screen-definition time and applied to an explicitly supplied actor. The default
   `_owner` is `lambda opener: Owner(opener.user_id)`, and a screen wanting
   `Everyone()` or a `Check` says so in its own definition.
2. **A guild scope requires a guild.** `Scope.GUILD` and `Scope.USER_GUILD` raise `TypeError`
   when `opener.guild_id is None`, at the call. The alternative is a `None` inside a
   `GuildScope`, which makes every DM collide with every other DM under one key.
3. **`parent=` collapses the attach/open branch.** When `sessions.session_for(parent)` returns
   a session, build the mount through `sessions.defaults.mount(...)` and route to
   `Session.attach`; otherwise route to `SessionRegistry.open`. `consent.py:526-539` is this
   branch, written by hand, and it is the one piece of consent's opening logic the banner
   site does not have — so the two sites are not merely duplicated but *divergent*.
4. **A screen does not word rejections.** `open` returns `OpenResult` unchanged.
   [34](34-safe-session-runtime.md) §B.3 gives hosts the rejection wording; the two consent
   sites word theirs differently on purpose (one replies, one uses a followup).

`Screen` holds no registry and no mount, so it starts no lifetime and lives as a module
constant.

## The bot

`squid/bot/consent.py` gains the shared value, and `consent_banner.py` imports it:

```python
CONSENT_SCREEN = Screen(
    "consent",
    policy=SessionPolicy(collision=Reject()),
    options={"timeout": 120},
)
```

Both call sites become one `await CONSENT_SCREEN.open(...)` plus their own `Rejected`
wording. The load-bearing comment is deleted, because the shared object now says what the
comment was asserting.

`squid/bot/ui.py` is unchanged: `create_mount`/`send_component` remain the non-session path,
and a screen is not involved in a command that simply replies with a panel.

## Considered, not done

- **A screen owning the `Rejected`/`Abandoned` triage.** Violates 34 §B.3. The repetition
  across the two sites is three lines of `isinstance`, and the wording that follows differs.
- **A screen holding a component factory.** The two consent sites build their component with
  different arguments, and one keeps the instance to `await component.wait()`. A factory field
  would serve neither, and would push the screen from a policy value toward a controller.
- **A screen registry (`bot.screens["consent"]`).** The module constant already *is* the
  shared identity. A lookup table adds a string that can go stale in exactly the way this
  plan exists to prevent.
- **Folding `Screen` into `MountDefaults`.** Different lifetimes, the same argument 43 used
  to keep `SessionPolicy` out: defaults are per host, a screen is per screen.
- **`Scope.CUSTOM`.** See above; a scope value an `Opener` cannot supply is a call for the
  registry API directly.

## Verification

- `Screen.key(opener)` equals the hand-written `SessionKey` for each of the four scopes, and
  the two guild scopes raise `TypeError` on a `None` guild.
- Opening through a screen yields a mount equal option-for-option to
  `defaults.mount(component, access=screen.access(opener), **screen.options)`, and per-call
  `overrides` win over `screen.options`.
- `parent=` attaches when the parent has a live session and opens a root when it does not,
  matching what `consent.py` does today.
- A screen with a non-default `access` (`Everyone()`) is honoured rather than overridden.
- `tests/test_screens.py`, `tests/test_sessions.py`, `tests/test_public_api.py`, the bot's
  consent tests (both sites still reject a second prompt with their own wording), then
  `just typecheck` and `git diff --check`.

## Status

Shipped 2026-08-23 — `Screen` and `Opener` are at `discord/screens.py:25,51`, with
`tests/test_screens.py`. Independent of [52](52-entity-selects.md) and
[53](53-view-adoption.md); first of the three because it is smallest and unblocks nothing.

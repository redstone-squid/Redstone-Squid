# 71 — Screen as a complete Discord opening façade

## Problem

`Screen` makes the common Discord entry point short, but its policy surface is not yet one
coherent abstraction:

- access is always owner-only even though both direct roots and `SessionSpec` support other
  policies;
- `session` contains a string rather than a session, while `options` does not say what it
  configures;
- changing one unsupported policy requires overriding and rebuilding the entire cached
  `SessionSpec`;
- session-only class fields and the `parent`/`key` arguments are silently ignored on paths where
  they cannot apply;
- `prepare()` duplicates `Component.on_load()` with a less explicit lifecycle; and
- `show()` checks single use before its first await but does not reserve the instance, so two
  concurrent calls may both proceed.

These are façade defects. The access, admission, lifecycle, and message-root layers beneath it
already have the required machinery.

## Decision

Keep the declarative class surface, with a strict split between root policy and optional session
policy.

```python
class Lobby(sd.Screen):
    session_name = "lobby"
    scope = sd.ScopeKind.GUILD
    access = sd.Everyone()
    visibility = "public"
    capacity = 4
    timeout = None
```

Root policy applies identically whether the screen opens directly or through a session:

- `access` is a fixed `AccessPolicy`, or `None` for the opener-only default;
- `resolve_access(invocation)` is the instance hook for policy derived from constructor state or
  the complete invocation;
- `visibility`, `timeout`, `expiry`, `follow_topics`, and `root_options` configure delivery and the
  message root.

Session policy is present only when `session_name` is not `None`: `scope`, `admission`, `capacity`,
`quota`, and `domain`. `session_name` replaces the misleading string-valued `session`, and
`root_options` replaces the context-free `options` name. A direct screen declaring session-only
policy fails when its class is created. Dedicated root fields may not also appear in
`root_options`.

`show()` keeps every opening option keyword-only, resolves access once, and applies that exact
policy to either opening path. It rejects
`parent` and `key` for direct screens, and rejects their ambiguous combination for session
screens. `wait` reaches both delivery paths; this adds the corresponding keyword to
`Invocation.mount()`.

The first `show()` call synchronously claims the component before resolving an invocation. A
second sequential or concurrent call fails with a message about `show()` having already been
called. The public `opening` property becomes available after invocation resolution and remains
available after a policy rejection, which describes the attempted opening accurately.

Remove `prepare()`. Invocation-dependent loading belongs in `on_load()`, which already runs once
before the first render and does not run for a session rejected before delivery. Remove public
`spec()` too: the façade derives its `SessionSpec` internally. Applications whose policy cannot be
expressed by the fields and access hook use `Invocation.open()` with a `SessionSpec`, the public
composition layer immediately below `Screen`.

## Rejected alternatives

- **A nested screen-policy dataclass.** It makes every ordinary screen longer without improving
  type safety. The class already scopes the declarations.
- **A second session-definition value.** It would duplicate most of `SessionSpec` merely to keep
  root policy elsewhere.
- **Putting `access` only on `SessionSpec`.** Direct and session screens would continue to expose
  different concepts through the same `show()` method.
- **A callable-or-policy union in `access`.** A separate `resolve_access()` hook keeps the common
  declaration simple and gives dynamic implementations an ordinary, precisely typed method.
- **Retrying the same instance after a failed show.** Delivery can fail after external state has
  changed. A fresh component makes retry state explicit and avoids duplicate mounts.

## Verification

- fixed and dynamically resolved access work for direct and session screens;
- `on_load()` sees `opening` and precedes the first render;
- concurrent `show()` calls admit exactly one attempt;
- invalid direct/session argument and declaration combinations fail immediately;
- root options and scheduled topic following reach both paths;
- application screens migrate without rebuilding a `SessionSpec`; and
- focused tests, the application callers, Pyrefly, naming checks, and `git diff --check` pass.

## Status

Shipped 2026-08-29. `Screen` now owns one root policy across direct and session openings, and the
layout-showcase lobby exercises public access without overriding the derived session recipe.

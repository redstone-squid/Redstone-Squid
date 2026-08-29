# squid-layouts migration: complete

The Redstone Squid bot now uses `squid_layouts` as its sole Discord UI design. The
migration completed on 2026-08-23 after the package namespace and delivery API settled.

## Shipped

- Bot responses and post reconciliation move complete `DiscordPresentation` values through
  `sl.discord.delivery`, including files, allowed mentions, and explicit edit handles.
- Public, durable controls use semantic `sl.Component` mounts with ownership, session keys,
  expiry, and framework-managed lifecycle. This includes accounts, consent, claims,
  diagnostics, notifications, settings, search, submissions, build editing, and polls.
- Transient text entry uses portable `sl.forms` specifications. Submission basics/details,
  build editing, poll creation/editing, custom poll duration, role weights, and vote emojis
  no longer construct native modal classes in the bot.
- Native compatibility view, modal, navigation, pagination, and message-boundary helpers were
  deleted once their production consumers moved. The remaining Discord-native types are only
  adapters at explicit package or frontend boundaries.
- Tests assert semantic mounts, portable form schemas, presentation delivery, and the
  Components V2 architecture guard. The guard's package exceptions are limited to intentional
  Discord adapter/type homes.

## Ongoing design rules

- Import the stable package-root semantic vocabulary as `import squid_layouts as sl`.
- Use deeper namespaces for specialized APIs: `sl.discord.delivery`,
  `sl.discord.presentation`, `sl.discord.modal`, `sl.discord.sessions`, and related
  `sl.discord.*` modules. The exact location of a specialized Discord API may move within
  that namespace while the package remains pre-1.0.
- Render bot-owned messages through `DiscordPresentation`; send and edit through a
  `Destination` or `EditHandle`. Do not call Discord message methods with `content`, `embed`,
  or `embeds` from bot workflows.
- Use a mounted semantic component for long-lived state and `sl.forms` for modal input.
  Keep business mutations in component callbacks, where the mount can serialize access,
  ownership, reactivity, expiry, and error handling.

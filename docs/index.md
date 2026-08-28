# Redstone Squid

Redstone Squid is a Discord bot that manages a database of Minecraft redstone records and
makes submitting, confirming, and denying build submissions easy. This site documents two
things that live in [one repository](https://github.com/redstone-squid/Redstone-Squid):

## The Squid UI framework

The reusable UI, reactivity, storage, and replication layers developed for the bot,
published on PyPI as six packages. Application code writes semantic components; the planner
produces an immutable, limits-checked scene; adapters draw it as Discord messages or native
accessible HTML.

- [Suite overview](squid-ui.md) — the packages and where to start
- [Discord quickstart](squid-ui-quickstart.md) — a first live screen
- [API map](squid-ui-api.md) — the supported entry point for each job
- [Architecture](squid-ui-architecture.md) — planning, ownership, cancellation, durability
- [Reference](reference/squid-ui.md) — every supported name, per package

## The bot

Operational documentation for Redstone Squid itself: internationalization, notifications,
error reporting, and database migration guides. Deployment instructions live in the
[repository README](https://github.com/redstone-squid/Redstone-Squid#readme).

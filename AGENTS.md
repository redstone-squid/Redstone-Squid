## Project: Redstone Squid Discord Bot

This is a Discord bot for managing Minecraft redstone build submissions, built with Python 3.12+ and discord.py. The bot manages a database of records, handles voting on submissions, and provides automated moderation features.

### Code Style
- **Formatting**: 120-character lines and Python 3.12 target
- **Documentation**: Google-style docstrings with type information
- **Type Safety**: Full type hints with BasedPyright for static analysis. Use your best judgement for when to `# type: ignore` and when to fix the typing issue.
- **Don't use Python 3.8 typings**: Never import `List`, `Tuple` or other deprecated classes from `typing`, use `list`, `tuple` etc. instead, or import from `collections.abc`
- Do not `from __future__ import annotations`, use forward references in type hints instead.
- Add code comments sparingly. Focus on why something is done, especially for complex logic. For unintuitive code, explain until it is clear.

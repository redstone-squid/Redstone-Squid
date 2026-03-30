## Project: Redstone Squid Discord Bot

This is a Discord bot for managing Minecraft redstone build submissions, built with Python 3.12+ and discord.py. The bot manages a database of records, handles voting on submissions, and provides automated moderation features.

### Architecture Decisions
- **Modern Python**: Python 3.12+ with async/await throughout the codebase
- **Discord.py**: Primary framework for Discord bot functionality with extensions (cogs)
- **Supabase**: PostgreSQL database with real-time features for data persistence
- **Runtime Validation**: Beartype for runtime type checking on critical paths
- **Docker**: Containerized deployment with multi-stage builds
- **Just**: Task runner for development workflows (justfiles over Makefiles)

### Code Style
- **Formatting**: Ruff with 120-character lines and Python 3.12 target
- **Testing**: pytest with asyncio support, markers for test categories (unit/integration/external)
- **Documentation**: Google-style docstrings with type information
- **Type Safety**: Full type hints with BasedPyright for static analysis, types must be fully parameterized if it is not possible to be inferred from usage.

### Patterns to Follow
- **Cog Architecture**: Organize bot commands into logical cogs/extensions
- **Separation of Concerns**: Use `squid.bot` for frontend (bot) logic, `squid.db` for database operations.

### Comments
Add code comments sparingly. Focus on why something is done, especially for complex logic, rather than what is done. Only add high-value comments if necessary for clarity or if requested by the user. Do not edit comments that are separate from the code you are changing. NEVER talk to the user or describe your changes through comments.

### What NOT to Do
- **Don't bypass type hints**: Avoid `Any` types unless strictly necessary
- **Don't skip error handling**: Always handle exceptions gracefully
- **Don't block the event loop**: Use asyncio primitives for concurrent operations
- **Don't use Python 3.8 typings**: Never import `List`, `Tuple` or other deprecated classes from `typing`, use `list`, `tuple` etc. instead, or import from `collections.abc`

## Extra Information

- The current database schema is in `schema_dump.sql` in the root directory.

## GitHub Actions & CI/CD

- When adding or changing GitHub Actions, always search online for the newest version and use the commit hash instead of version tags for security and immutability. (Use `gh` CLI to find the commit hash, searching won't give you helpful results.)
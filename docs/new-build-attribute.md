Steps for adding a new attribute to the Build object:
1. Add the attribute to `Build` in `squid/builds/domain/models.py`.
2. Update the matching build infrastructure model and add a [new migration](new-migration.md).
3. Update the domain-to-persistence mapping in `squid/builds/infrastructure/repository.py`.
4. Update the application submission or edit DTO that accepts the attribute.
5. See if squid.bot.submission has commands that need to be updated to handle the new attribute.

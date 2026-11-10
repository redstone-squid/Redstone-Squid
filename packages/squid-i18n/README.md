# Squid i18n

Declare named Fluent messages in Python t-strings. Captured values remain data;
literal text and typed formatting expressions compile into Fluent syntax.

```python
from squid_i18n import NUMBER, tr

count = 2
message = tr(t"build-count = {NUMBER(count)} builds")
```

Messages resolve only when a reader's localization is available. See the repository's
`docs/i18n.md` for authoring, extraction, catalog validation, and translation updates.

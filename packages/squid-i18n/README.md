# Squid i18n

Declare named Fluent messages in Python t-strings. Captured values remain data;
literal text and typed formatting expressions compile into Fluent syntax.

```python
from squid_i18n import NUMBER, md_tr, tr

count = 2
message = tr(t"{NUMBER(count)} builds", key="build-count")
label = tr("Save", key="save")
formatted = md_tr(t"**{NUMBER(count)}** builds", key="build-count-formatted")
```

All constructors return `Text`. Markdown is parsed after translation; interpolated
values remain literal and rendering chooses the destination syntax.

Messages resolve only when a reader's localization is available. See the repository's
`docs/i18n.md` for authoring, extraction, catalog validation, and translation updates.

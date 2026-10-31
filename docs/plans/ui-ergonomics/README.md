# UI ergonomics spike — superseded

The design spike recorded in `363e4932` is superseded by the shipped `Invocation` and
`Screen` APIs. The maintained entry-point guidance and ownership rules now live in
[`docs/squid-ui-architecture.md`](../../squid-ui-architecture.md#which-entry-point-to-use).

Keep this directory only as the historical pointer for decisions made during the spike;
new application code should enter through `Invocation.reply`/`mount`/`open` or a declarative
`screen.show`.

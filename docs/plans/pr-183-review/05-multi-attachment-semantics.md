# Multi-Attachment Semantics

## Findings

- Attachment classification now correctly distinguishes image, video, and schematic results, and its error text no longer calls every rejected file a schematic. The repository makes no claim about which Discord clients produce missing or generic content types; extension-first classification is required by the accepted input contract and tests prove both inputs are handled.
- Submission accepts four attachments but silently makes the first successfully analysed schematic primary. That order also controls dimension prefilling, mismatch evidence, duplicate checking, and later render eligibility.
- Duplicate lookup still examines only `analyses[0]`. Duplicate evidence is stored as build IDs and tiers; the build card formats those IDs rather than presenting useful candidate summaries or links.
- Images/videos are uploaded immediately, whereas schematic bytes are analysed before the form completes. Partial upload/analyse failures need an explicit user-visible policy.

## Intended changes

- Make primary selection explicit in the submission workspace after classification/analysis. Default to the only schematic when there is one; require a user choice when multiple usable schematics exist rather than relying on upload order.
- Represent analysed attachments with a typed object carrying attachment identity, original filename, classification, analysis/failure, and primary selection. Pass that object through prefill, duplicate detection, persistence, and rendering.
- Check every successfully analysed schematic for duplicates, merge candidates by build and strongest match, and retain which submitted attachment produced each match. Fetch compact build summaries for the review UI instead of displaying bare IDs.
- Define partial-failure behavior: keep media uploads usable, show per-file schematic failures, allow submission without failed enrichments, and never silently substitute another file as primary.
- Keep Discord `content_type` wording limited to the repository-proven input contract unless a captured fixture or authoritative API documentation supports a stronger claim; retain extension-first handling.

## Tests

- Scenarios cover zero, one, and several schematics; primary selection; reordered attachments; one failed analysis; and all analyses failing.
- Duplicate tests cover matches from non-first files, repeated candidates across files, strongest-tier merging, and build-summary rendering.
- Classification tests cover missing, generic, misleading, and correctly specific content types for schematics and non-schematics.

## Completion update (2026-08-30)

**Done.** Attachments retain stable identity, classification, analysis, failure, and primary facts.
Multiple usable schematics require an explicit choice; the sole usable schematic defaults safely.
Duplicate evidence covers every successful analysis, merges by strongest match with titled source
summaries, and records partial lookup failures. Same-digest uploads coalesce without upload-order
effects, while post-save record failures become persisted recovery evidence and truthful UI.
Zero/one/many, reorder, one/all-failure, record-failure, boundary, and nested JSON round-trip cases
are present; PostgreSQL execution of the round trip remains externally gated.

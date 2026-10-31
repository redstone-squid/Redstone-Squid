# Multi-Attachment Semantics

## Findings

- Attachment classification now correctly distinguishes image, video, and schematic results, and its error text no longer calls every rejected file a schematic. The test claim about Discord omitting schematic content types remains an external observation; extension-first classification is nevertheless required by the accepted upload contract.
- Submission accepts four attachments but silently makes the first successfully analysed schematic primary. That order also controls dimension prefilling, mismatch evidence, duplicate checking, and later render eligibility.
- Duplicate lookup still examines only `analyses[0]`. Duplicate evidence is stored as build IDs and tiers; the build card formats those IDs rather than presenting useful candidate summaries or links.
- Images/videos are uploaded immediately, whereas schematic bytes are analysed before the form completes. Partial upload/analyse failures need an explicit user-visible policy.

## Intended changes

- Make primary selection explicit in the submission workspace after classification/analysis. Default to the only schematic when there is one; require a user choice when multiple usable schematics exist rather than relying on upload order.
- Represent analysed attachments with a typed object carrying attachment identity, original filename, classification, analysis/failure, and primary selection. Pass that object through prefill, duplicate detection, persistence, and rendering.
- Check every successfully analysed schematic for duplicates, merge candidates by build and strongest match, and retain which submitted attachment produced each match. Fetch compact build summaries for the review UI instead of displaying bare IDs.
- Define partial-failure behavior: keep media uploads usable, show per-file schematic failures, allow submission without failed enrichments, and never silently substitute another file as primary.
- Verify Discord `content_type` behavior with a small captured/integration fixture or authoritative API documentation; keep extension-first handling unless evidence supports a stricter signal.

## Tests

- Scenarios cover zero, one, and several schematics; primary selection; reordered attachments; one failed analysis; and all analyses failing.
- Duplicate tests cover matches from non-first files, repeated candidates across files, strongest-tier merging, and build-summary rendering.
- Classification tests cover missing, generic, misleading, and correctly specific content types for schematics and non-schematics.

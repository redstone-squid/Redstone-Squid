# Audit of unresolved build tags

The November 15, 2026 historical import snapshot (`historical-final.dump`) contains
**9,189 unresolved entries across 5,294 builds**. The snapshot predates bulk approval
and contains 9,060 pending and seven rejected build rows. Eight unresolved entries
are on rejected builds. The audit uses the catalogue from the same snapshot.

An entry is one normalized value per build and source field. Normalization folds
case and collapses whitespace, as the resolver does. Distinct values are counted
within each field.

| Source field | Distinct values | Entries |
| --- | ---: | ---: |
| Patterns | 407 | 1,915 |
| Wiring placement restrictions | 280 | 2,574 |
| Component restrictions | 690 | 2,701 |
| Animated restrictions | 245 | 556 |
| Miscellaneous restrictions | 588 | 1,443 |
| **Total** | **2,210** | **9,189** |

## Initial recommendations

1. **Add straightforward aliases.** 495 entries match candidate aliases for
   tags already applicable to the build. Start with `non-loc` → **Not
   Locational**, `non-dir` → **Not Directional**, `loc` → **Locational**,
   `dir` → **Directional**, `glass center` → **Center Glass**,
   `observer-less` → **No Observers**, and `semi-flush` → **Semi Flush**.
   Each target also has other observed spellings. The inventory flags 25 more
   entries whose alias would also need broader applicability.
2. **Add the missing tags already defined in the rules.** **Glass** is a
   pattern (585 entries), **Contained** is a component restriction (403
   entries), and **Full Sync**, **Super Sync**, and **Semi Sync** are animated
   restrictions (63 entries under the animated field). Use unqualified
   `sync` and `synced` as review cues because their tier is unspecified.
   See the [door rules](../door-rules.md).
3. **Extend existing tags to the categories where their definitions apply.**
   385 entries already match a name or alias but are on extenders, utilities,
   or entrances. The snapshot's official catalogue applies only to doors.
   Start with broadly applicable component restrictions such as **No
   Observers** (104 entries) and **No Entities** (59). Check the geometry
   definitions before extending **Flush** (53) or **Staircase** (20).
4. **Assess additional component tags.** The inventory identifies 1,242
   entries across all new-tag candidates, including the rule-defined tags.
   The next explicit component omissions are **No Redstone Dust** (44),
   **No Jukeboxes** (34), **No Comparators** (30), and **No Target Blocks**
   (27). Agree on scope and applicable build kinds before backfilling.

## Terms flagged for review

| Observed family | Entries | Proposed treatment |
| --- | ---: | --- |
| Unqualified seamlessness and misspellings | 1,227 | Check the source definition before assigning a tier. |
| Hipster without a clear surface or tier | 351 | Recover the floor, ceiling, or wall and full or semi from source evidence. |
| Negative seamlessness forms | 324 | Keep the original claim until there is a reason to make it a searchable restriction. |
| Sandless and No Sand | 301 | Check whether sources exclude sand specifically or all gravity blocks. |
| Shutter, Spiral, and Cracked | 208 | Check whether each occurrence describes motion, construction, or the block layout. |
| TNT | 168 | Resolve to Full TNT or Semi TNT when the source supports it. |
| Infinite expandability variants | 86 | Preserve the finite/infinite distinction from the rules. |
| Dual Corner and Double Corner | 42 | Check whether Dual plus Corner or Dual Cave Corner describes the build. |

There are also 35 entries matching an official name in the other semantic
kind. For example, `Zomba` in patterns can move to component restrictions
when the source supports that reading. For `Sand` in component restrictions,
confirm the source before moving it to patterns.

Use structured timings and record criteria for values such as `0.45s` and
`smallest` where supported. Keep subjective animation descriptions and
build-specific qualifiers as notes. The inventory flags 239 entries for
structured fields or notes. A further 3,540 entries are outside the curated
proposals and need individual review.

After approving changes, re-run tag resolution on affected builds
through the build write path so assignments and unresolved values are updated
together. Preview the changes by build kind and preserve the raw value wherever
the evidence is ambiguous.

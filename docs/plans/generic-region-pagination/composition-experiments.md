# Semantic planning and composition experiments

These Phase 1 experiments establish information the shared core needs. They exercise the
current planners, not a proposed implementation. Target limits below are deliberately reduced
for small examples; they are not assertions about external service limits.

## Tables retain information that output scenes cannot recover

An authored `Table` contains a table key, column keys, row keys, cells, display choice, and
flexibility. Discord's table strategy selection reads display and flexibility, but lowering
turns rows into code text or a sequence of record strings. Row keys do not survive that lowering
as row anchors. HTML emits table elements without those row/column keys, display choice, or
flexibility. Slack consumes the HTML structure, after that information has been discarded.

A direct probe used three columns and three data rows, once with automatic display and once
with records explicitly requested. Slack's local caps were reduced to two columns and three
rows. Both requests produced this visible table, including its header:

```text
A   B
A1  B1
A2  B2
```

Column C and data row 3 were absent from the visible table. Planning succeeded with `strict=True`
and no diagnostic events. Slack's separate accessibility fallback text retained all the input
cells, so searching the whole serialized scene for text would have missed this defect.

For the same input, HTML and V2 retained all cell values. HTML emitted a table even when records
were requested. V2's records strategy produced labeled record text. These observations concern
content and representation; they do not establish correct anchor preservation on any target.

The Slack row and column slices occur directly in `_Lowerer._table`; neither slice adds a
content-loss event. This violates the newly agreed policy and needs a regression case during
migration. It is recorded here rather than changing a production backend during the design phase.

Run the visible-table probe with `uv run --no-sync python` and this input:

```python
from dataclasses import replace
import squid_ui as sl
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.target_types import SlackAdapter

target = sl.slack.message_target(adapter=AdapterProfile(SlackAdapter, "probe.slack", "1"))
target = replace(
    target,
    limits=replace(
        target.limits,
        components=replace(target.limits.components, table_columns=2, table_rows=3),
    ),
)
columns = sl.columns(*(sl.column(key, key=key) for key in ("A", "B", "C")))
rows = tuple(
    sl.table_row(*(f"{key}{index}" for key in ("A", "B", "C")), key=f"row-{index}")
    for index in range(1, 4)
)
for display in (sl.semantic.TableDisplay.AUTO, sl.semantic.TableDisplay.RECORDS):
    result = sl.planning.plan(sl.table(columns, *rows, key="sample", display=display), target=target, strict=True)
    print(display, [[cell.content for cell in row] for row in result.scene.body.blocks[0].rows])
    print([event.code for event in result.report.events])
```

Requirement: the core must retain semantic units and relationships while choosing representations
and breaks. A target must report which units a representation preserves, changes, or omits. The
core must also retain enough structure to validate interactions and row/column associations;
counting strings or comparing output text is not a general preservation test.

## Shared attachments invalidate count-only composition

Two download controls reference the same `Asset`. With room for one attachment, the ordinary
V2 planner returns a scene with one shared asset. Wrapping each download in its own region
instead raises `UnsolvableLayoutError`. Wrapping both in an unbreakable stack inside one region
also fails; that wrapper does not make the current solver measure the two references together.

The ordinary result's `cost` does not include its attachment count in this example. The region
solver adds attachment counts to its leaf measurements, then sums those counts when composing
leaves. This both exposes inconsistent cost reporting and loses the sharing relationship.

Reproduce with `uv run --no-sync python` and this input:

```python
import squid_ui as sl
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import components_v2_target
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.target_types import DiscordAdapter

target = components_v2_target(AdapterProfile(DiscordAdapter, "probe.discord", "1"))
target = target.reserve(ResourceCost({Axis.ATTACHMENTS: 9}))
asset = sl.file(b"shared", filename="shared.bin")
left, right = sl.download(asset, key="left"), sl.download(asset, key="right")
for content in (
    (left, right),
    (sl.region(left, key="left-region", title="Left"), sl.region(right, key="right-region", title="Right")),
    sl.region(sl.unbreakable(sl.stack(left, right)), key="together", title="Together"),
):
    try:
        result = sl.planning.plan(content, target=target)
        print(len(result.scene.assets), dict(result.cost.values))
    except sl.errors.LayoutError as error:
        print(type(error).__name__, str(error))
```

This is also a counterexample to pruning candidates solely by their standalone resource counts.
Suppose candidate A references asset X and candidate B references asset Y. Both cost one
attachment alone. In combination with a fixed sibling referencing X, A costs one distinct
attachment and B costs two. They are not interchangeable despite equal standalone vectors.

Requirement: planning must retain resource identity and dependencies until composition can
account for sharing. Pruning needs a justified comparison in the relevant context; it cannot
infer interchangeability from equal standalone counts. This does not prescribe a set-based
representation for every resource or settle the solver algorithm.

## Nested regions and state

The existing region suite was rerun alongside the HTML, Slack, and sequence-breaking tests:
62 tests passed. Region cases exercise every page combination of a small pair of siblings,
retained positions for hidden nested regions, keyed insertion, controlled and routed ownership,
failed delivery, stale controls, and committed allocation reuse.

Those tests establish behavior to retain. They do not prove that existing per-axis envelopes
handle shared resources or arbitrary composition. The attachment counterexample fails before
any page turn. Likewise, keeping a cursor does not by itself establish that a new representation
preserves the semantic item it previously anchored.

Requirement: keep the identities of semantic items separate from occurrence paths, chosen
representation, page partitions, and committed cursor state. The final design must establish
which identifiers remain stable through each transformation and which changes trigger replanning.
The [phase plan](01-shared-core.md) records the subsequent state probes and representative behavior matrix.

## Information the experiments require

| Information | Why shared planning needs it |
| --- | --- |
| Semantic units and relationships | Preserve table rows, heading associations, reading order, and interaction scope through transformations. |
| Authored policies and representation preferences | Distinguish permitted changes from target constraints and mere formatting choices. |
| Candidate content and behavior effects | Determine whether a representation is admissible without relying on final scene text. |
| Resource identities and composition dependencies | Account for sharing and avoid unsound candidate pruning. |
| Container and continuation requirements | Include repeated headings, wrappers, navigation, and context-dependent costs in allocation. |
| Stable identity and staged state | Reconcile anchors and publish only the controls and positions that were delivered. |

A possible model keeps the authored semantic graph and associates planning decisions and target
measurements with its occurrences, producing concrete scene data only after selection. Whether
those associations are annotations, a derived graph, or another representation remains open.
The experiments justify retaining information; they do not establish a particular type hierarchy.

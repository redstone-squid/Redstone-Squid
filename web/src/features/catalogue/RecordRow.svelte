<script lang="ts">
  import type { RecordCriterion, RecordHolderSummary, RecordMetric } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import Text from "@/ui/content/Text.svelte";
  import ListItem from "@/ui/data/ListItem.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import BuildPreview from "./BuildPreview.svelte";
  import { buildName, holderBuild, recordHref, recordMeasurements } from "./presentation";

  /**
   * A record as one row of a `List`, for the records a build or creator holds. It shows what
   * the data carries: the holder's thumbnail and name from a record summary, the headline
   * number when there are metrics, and since when a build has held it, or that it was lost.
   */
  type Props = {
    record: {
      record_id: string;
      title: string;
      subtitle?: string | null;
      criterion?: RecordCriterion;
      metrics?: Record<string, RecordMetric>;
      holders?: RecordHolderSummary[];
      since?: string | null;
      retired?: boolean;
    };
    /** The build lost this record, for a build's former records. */
    former?: boolean;
  };
  let { record, former = false }: Props = $props();
  const holders = $derived(record.holders ?? []);
  const measurements = $derived(
    record.criterion === undefined
      ? []
      : recordMeasurements({ criterion: record.criterion, metrics: record.metrics }).slice(0, 1),
  );
  const description = $derived(
    holders.length > 0
      ? formatList(holders.map((holder) => buildName(holder)))
      : (record.subtitle ?? undefined),
  );
</script>

{#snippet thumbnail()}
  {#if holders[0]}
    <BuildPreview build={holderBuild(holders[0])} thumbnail="square" class="size-10 rounded-xs" />
  {/if}
{/snippet}
{#snippet status()}
  {#if record.retired === true}
    <Badge>{m.recordRetiredShort()}</Badge>
  {:else if former}
    <Badge>{m.recordFormer()}</Badge>
  {/if}
  {#if record.since != null}
    <Text as="span" size="xs" tone="muted">{m.recordHeldSince({ date: record.since })}</Text>
  {/if}
{/snippet}
{#snippet headline()}
  <StatGroup layout="inline" size="sm" items={measurements} />
{/snippet}

<ListItem
  title={record.title}
  href={recordHref(record.record_id)}
  {description}
  leading={holders.length > 0 ? thumbnail : undefined}
  meta={record.retired === true || former || record.since != null ? status : undefined}
  trailing={measurements.length > 0 ? headline : undefined}
/>

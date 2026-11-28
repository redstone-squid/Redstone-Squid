<script lang="ts">
  import type { PublicRecordSummary, RecordFact } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import { recordFactLabel, versionScopeLabel } from "@/locales/vocabulary.gen";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Layout from "@/ui/layout/Layout.svelte";

  /**
   * A record as a card linking to its page. The title already names the build kind, and
   * the version scope shows only when it narrows the record to the current version. A
   * single holder goes unmentioned; shared records and ties are called out.
   */
  type Props = {
    record: PublicRecordSummary;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
  };
  let { record, as = "article", headingLevel = 3 }: Props = $props();
  const shared = $derived(record.holders.length);
  const tied = $derived(record.tied_build_ids?.length ?? 0);
  const measurements = $derived.by(() => {
    const metrics = record.metrics ?? {};
    const timings: [string, RecordFact][] = [
      ["opening_time", "opening"],
      ["visible_opening_time", "opening_visible"],
      ["closing_time", "closing"],
      ["visible_closing_time", "closing_visible"],
      ["opening_reset_time", "opening_reset"],
      ["closing_reset_time", "closing_reset"],
      ["retraction_time", "retraction"],
      ["extension_time", "extension"],
      ["retraction_reset_time", "retraction_reset"],
      ["extension_reset_time", "extension_reset"],
    ];
    const timing = timings.find(([key]) => metrics[key] !== undefined);
    const metric = timing && metrics[timing[0]];
    const speed =
      timing && metric
        ? [{ term: recordFactLabel(timing[1]), value: metric.display, numeric: true }]
        : [];
    const volume = metrics.volume
      ? [{ term: recordFactLabel("fixed_volume"), value: metrics.volume.display, numeric: true }]
      : [];
    switch (record.criterion) {
      case "first": {
        return metrics.completion_at
          ? [{ term: recordFactLabel("completion_at"), value: metrics.completion_at.display }]
          : [];
      }
      case "smallest": {
        return volume;
      }
      case "fastest": {
        return speed;
      }
      case "fastest_smallest": {
        return [...volume, ...speed];
      }
      case "smallest_fastest": {
        return [...speed, ...volume];
      }
    }
  });
  const facts = $derived([
    ...(shared > 1 ? [m.recordSharedCount({ count: shared })] : []),
    ...(tied > 0 ? [m.recordTiedCount({ count: tied })] : []),
    ...(record.version_scope === "up_to_date" ? [versionScopeLabel(record.version_scope)] : []),
  ]);
</script>

<Card {as} title={record.title} href={localizePath(`/records/${record.record_id}`)} {headingLevel}>
  {#if record.subtitle}<Text size="sm" tone="muted">{record.subtitle}</Text>{/if}
  {#if measurements.length > 0}<DescriptionList layout="inline" items={measurements} />{/if}
  {#if facts.length > 0 || record.refresh_pending === true}
    <Layout pattern="cluster" gap="sm" align="center">
      <MetaList items={facts} />
      {#if record.refresh_pending === true}
        <Badge tone="info" dot>{m.recordRefreshPending()}</Badge>
      {/if}
    </Layout>
  {/if}
</Card>

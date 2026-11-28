<script lang="ts">
  import type { PublicRecordSummary } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import { versionScopeLabel } from "@/locales/vocabulary.gen";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import BuildMention from "./BuildMention.svelte";
  import { holderBuild, recordHref, recordMeasurements } from "./presentation";

  /**
   * A record as a card linking to its page: the numbers that decide it, and its holder in the
   * footer. The title already names the build kind, and the version scope shows only when it
   * narrows the record to the current version. Shared records and ties are called out.
   */
  type Props = {
    record: PublicRecordSummary;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
  };
  let { record, as = "article", headingLevel = 3 }: Props = $props();
  const tied = $derived(record.tied_build_ids?.length ?? 0);
  const facts = $derived([
    ...(record.holders.length > 1 ? [m.recordSharedCount({ count: record.holders.length })] : []),
    ...(tied > 0 ? [m.recordTiedCount({ count: tied })] : []),
    ...(record.version_scope === "up_to_date" ? [versionScopeLabel(record.version_scope)] : []),
  ]);
</script>

<Card {as} title={record.title} href={recordHref(record.record_id)} {headingLevel}>
  {#if record.subtitle}<Text size="sm" tone="muted">{record.subtitle}</Text>{/if}
  <StatGroup layout="inline" lead items={recordMeasurements(record)} />
  {#if facts.length > 0 || record.refresh_pending === true}
    <Layout pattern="cluster" gap="sm" align="center">
      {#if facts.length > 0}<MetaList items={facts} />{/if}
      {#if record.refresh_pending === true}
        <Badge tone="info" dot>{m.recordRefreshPending()}</Badge>
      {/if}
    </Layout>
  {/if}
  {#snippet footer()}
    {#if record.holders.length === 1 && record.holders[0]}
      <BuildMention build={holderBuild(record.holders[0])} layout="block" />
    {:else if record.holders.length > 1}
      <Layout as="ul" pattern="cluster" gap="md">
        {#each record.holders as holder (holder.build_id)}
          <li><BuildMention build={holderBuild(holder)} /></li>
        {/each}
      </Layout>
    {:else}
      <Text size="sm" tone="muted">{m.recordNoHolders()}</Text>
    {/if}
  {/snippet}
</Card>

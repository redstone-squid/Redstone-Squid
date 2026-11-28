<script lang="ts">
  import type { PublicRecordSummary } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Pagination from "@/ui/navigation/Pagination.svelte";
  import RecordCard from "./RecordCard.svelte";

  /** One page of records with links to the adjacent pages, which the page computes. */
  type Props = {
    records: PublicRecordSummary[];
    prevHref: string | null;
    nextHref: string | null;
  };
  let { records, prevHref, nextHref }: Props = $props();
</script>

<Layout pattern="stack">
  {#if records.length === 0}
    <EmptyState title={m.recordsEmpty()} />
  {:else}
    <Layout as="ul" pattern="grid" min="card-wide">
      {#each records as record (record.record_id)}
        <RecordCard {record} as="li" headingLevel={2} />
      {/each}
    </Layout>
  {/if}
  <Pagination
    {prevHref}
    {nextHref}
    labels={{ nav: m.navPagination(), previous: m.commonPrevious(), next: m.commonNext() }}
  />
</Layout>

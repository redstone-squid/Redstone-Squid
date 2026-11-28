<script lang="ts">
  import type { PublicRecordSummary } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import { buildKindLabel, versionScopeLabel } from "@/locales/vocabulary.gen";
  import Text from "@/ui/content/Text.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import DataTable from "@/ui/table/DataTable.svelte";
  import type { DataTableColumn, DataTableSort, SortDirection } from "@/ui/table/types";
  import BuildMention from "./BuildMention.svelte";
  import {
    buildName,
    holderBuild,
    recordHref,
    recordMeasurements,
    tableLabels,
  } from "./presentation";

  /**
   * Records as a scannable leaderboard: each record's deciding number, holder, and since when
   * it has been held. Sorting and hidden columns are links the page answers: the sort keys
   * are `title` and `since`, matching the API's `title` and `recently_changed`.
   */
  type Props = {
    records: PublicRecordSummary[];
    caption: string;
    captionHidden?: boolean;
    sort?: DataTableSort;
    sortHref?: (key: string, direction: SortDirection) => string;
    hidden?: string[];
    visibilityHref?: (hidden: string[]) => string;
  };
  let {
    records,
    caption,
    captionHidden = false,
    sort = null,
    sortHref,
    hidden = [],
    visibilityHref,
  }: Props = $props();

  const columns: DataTableColumn<PublicRecordSummary>[] = [
    {
      id: "record",
      header: m.catalogueRecord(),
      value: (record) => record.title,
      sortKey: "title",
      width: "lg",
    },
    {
      id: "value",
      header: m.catalogueRecordValue(),
      value: (record) => recordMeasurements(record)[0]?.value ?? null,
      numeric: true,
    },
    {
      id: "holder",
      header: m.catalogueBuild(),
      value: (record) => record.holders.map(buildName).join(", "),
      width: "md",
    },
    {
      id: "since",
      header: m.catalogueRecordSince(),
      value: (record) =>
        record.holder_since != null ? m.catalogueDate({ date: record.holder_since }) : null,
      sortKey: "since",
      width: "sm",
      hideable: true,
    },
    {
      id: "kind",
      header: m.recordBuildKind(),
      value: (record) => buildKindLabel(record.build_kind),
      hideable: true,
    },
    {
      id: "versions",
      header: m.recordVersionScope(),
      value: (record) => versionScopeLabel(record.version_scope),
      hideable: true,
    },
  ];
</script>

<DataTable
  {caption}
  {captionHidden}
  {columns}
  rows={records}
  getRowId={(record) => record.record_id}
  rowHref={(record) => recordHref(record.record_id)}
  {sort}
  {sortHref}
  {hidden}
  {visibilityHref}
  labels={tableLabels()}
>
  {#snippet cell(record, column)}
    {#if column.id === "record"}
      <span class="grid">
        <span>{record.title}</span>
        {#if record.subtitle}<Text as="span" size="xs" tone="muted">{record.subtitle}</Text>{/if}
      </span>
    {:else if column.id === "holder"}
      {#if record.holders.length > 0}
        <Layout pattern="stack" gap="2xs">
          {#each record.holders as holder (holder.build_id)}
            <BuildMention build={holderBuild(holder)} />
          {/each}
        </Layout>
      {:else}
        <Text as="span" size="sm" tone="muted">{m.recordNoneYet()}</Text>
      {/if}
    {:else}
      {column.value(record) ?? ""}
    {/if}
  {/snippet}
  {#snippet empty()}
    <EmptyState title={m.recordsEmpty()} />
  {/snippet}
</DataTable>

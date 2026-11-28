<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import Badge from "@/ui/feedback/Badge.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import DataTable from "@/ui/table/DataTable.svelte";
  import type { DataTableColumn, DataTableSort, SortDirection } from "@/ui/table/types";
  import BuildPreview from "./BuildPreview.svelte";
  import { buildHref, buildName, buildSize, buildVolume, tableLabels, ticks } from "./presentation";

  /**
   * Builds side by side for comparing measurements, such as the doors competing for a record.
   * Sorting and hidden columns are links the page answers: the sort keys are `title`,
   * `volume`, `opening`, and `submitted`, and `sortHref` maps them to the API's sort.
   * `highlight` marks builds by id with `highlightLabel`, such as the current holder.
   */
  type Props = {
    builds: PublicBuildSummary[];
    caption: string;
    captionHidden?: boolean;
    sort?: DataTableSort;
    sortHref?: (key: string, direction: SortDirection) => string;
    hidden?: string[];
    visibilityHref?: (hidden: string[]) => string;
    highlight?: { ids: number[]; label: string };
  };
  let {
    builds,
    caption,
    captionHidden = false,
    sort = null,
    sortHref,
    hidden = [],
    visibilityHref,
    highlight,
  }: Props = $props();

  const columns: DataTableColumn<PublicBuildSummary>[] = [
    { id: "build", header: m.catalogueBuild(), value: buildName, sortKey: "title", width: "lg" },
    {
      id: "creators",
      header: m.buildCreators(),
      value: (build) => formatList(build.creators.map((creator) => creator.name)),
      truncate: true,
      hideable: true,
    },
    {
      id: "size",
      header: m.buildDimensions(),
      value: (build) => buildSize(build.dimensions),
      numeric: true,
      hideable: true,
    },
    {
      id: "volume",
      header: m.catalogueVolume(),
      value: (build) => {
        const volume = buildVolume(build.dimensions);
        return volume === null ? m.buildTimingUnknown() : m.buildBlocks({ count: volume });
      },
      numeric: true,
      sortKey: "volume",
      hideable: true,
    },
    {
      id: "opening",
      header: m.buildOpening(),
      value: (build) => ticks(build.opening_time),
      numeric: true,
      sortKey: "opening",
    },
    {
      id: "closing",
      header: m.buildClosing(),
      value: (build) => ticks(build.closing_time),
      numeric: true,
      hideable: true,
    },
    {
      id: "versions",
      header: m.recordVersionScope(),
      value: (build) => build.version_spec ?? formatList(build.versions),
      hideable: true,
    },
    {
      id: "submitted",
      header: m.catalogueSubmitted(),
      value: (build) =>
        build.created_at === null ? "" : m.catalogueDate({ date: build.created_at }),
      sortKey: "submitted",
      hideable: true,
    },
  ];
</script>

<DataTable
  {caption}
  {captionHidden}
  {columns}
  rows={builds}
  getRowId={(build) => String(build.id)}
  rowHref={(build) => buildHref(build.id)}
  {sort}
  {sortHref}
  {hidden}
  {visibilityHref}
  labels={tableLabels()}
>
  {#snippet cell(build, column)}
    {#if column.id === "build"}
      <Layout pattern="cluster" gap="xs" align="center" wrap={false}>
        <BuildPreview {build} thumbnail="square" class="size-8 shrink-0 rounded-xs" />
        <span>{buildName(build)}</span>
        {#if highlight?.ids.includes(build.id)}
          <Badge tone="success">{highlight.label}</Badge>
        {/if}
      </Layout>
    {:else}
      {column.value(build) ?? ""}
    {/if}
  {/snippet}
  {#snippet empty()}
    <EmptyState title={m.catalogueBuildsEmpty()} />
  {/snippet}
</DataTable>

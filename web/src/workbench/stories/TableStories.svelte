<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Badge from "@/ui/feedback/Badge.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Pagination from "@/ui/navigation/Pagination.svelte";
  import DataTable from "@/ui/table/DataTable.svelte";
  import type { DataTableColumn } from "@/ui/table/types";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  type Build = {
    id: string;
    title: string;
    size: string;
    volume: number;
    opening: number;
    status: "Confirmed" | "Pending";
    creators: string;
    builds?: Build[];
  };

  const builds: Build[] = [
    {
      id: "copper",
      title: m.storyCopperBolt55(),
      size: "9 × 7 × 4",
      volume: 252,
      opening: 8,
      status: "Confirmed",
      creators: "CircuitSage, Space Builder",
    },
    {
      id: "iris",
      title: m.storyIrisDoorWithAMuchLongerNameThan(),
      size: "11 × 11 × 5",
      volume: 605,
      opening: 12,
      status: "Pending",
      creators: "红石猫",
    },
    {
      id: "flush",
      title: m.storyFlushPistonDoor(),
      size: "6 × 5 × 3",
      volume: 90,
      opening: 4,
      status: "Confirmed",
      creators: "Quartz",
    },
  ];
  const columns: DataTableColumn<Build>[] = [
    { id: "title", header: m.storyBuild(), value: (row) => row.title, width: "lg", truncate: true },
    {
      id: "status",
      header: m.storyStatus(),
      value: (row) => (row.status === "Confirmed" ? m.storyConfirmed() : m.storyPendingLabel()),
      hideable: true,
    },
    {
      id: "size",
      header: m.storySize(),
      value: (row) => row.size,
      mono: true,
      sortKey: "volume",
      hideable: true,
    },
    {
      id: "opening",
      header: m.buildOpening(),
      value: (row) => m.buildTicks({ ticks: row.opening }),
      numeric: true,
      sortKey: "opening",
    },
    {
      id: "creators",
      header: m.buildCreators(),
      value: (row) => row.creators,
      width: "md",
      hideable: true,
    },
  ];
  const sortHref = (key: string, direction: string) =>
    `?sort=${direction === "desc" ? "-" : ""}${key}#datatable`;
  const labels = {
    expand: m.storyShowBuilds(),
    collapse: m.storyHideBuilds(),
    loading: m.storyLoadingLabel(),
    visibility: {
      menu: m.storyColumns(),
      show: (header: string) => m.storyShowColumn({ header }),
      hide: (header: string) => m.storyHideColumn({ header }),
    },
  };
  const visibilityHref = (hidden: string[]) =>
    hidden.length > 0 ? `?hide=${hidden.join(",")}#datatable` : "#datatable";

  const records: Build[] = [
    {
      id: "smallest-5x5",
      title: m.storySmallest55PistonDoor(),
      size: "—",
      volume: 252,
      opening: 8,
      status: "Confirmed",
      creators: m.story2Holders(),
      builds: builds.slice(0, 2),
    },
    {
      id: "fastest-3x3",
      title: m.storyFastest33PistonDoor(),
      size: "—",
      volume: 90,
      opening: 4,
      status: "Confirmed",
      creators: m.story1Holder(),
      builds: builds.slice(2),
    },
  ];
</script>

<Story name="DataTable" source="table/DataTable" description={m.storyDataTableDescription()}>
  <Variant label={m.storySortedByOpeningLinkedRowsCustomCells()}>
    <DataTable
      caption={m.storyDoorBuilds()}
      {columns}
      rows={builds}
      getRowId={(row) => row.id}
      sort={{ key: "opening", direction: "asc" }}
      {sortHref}
      rowHref={(row) => `#datatable-${row.id}`}
      {labels}
    >
      {#snippet cell(row, column)}
        {#if column.id === "status"}
          <Badge tone={row.status === "Confirmed" ? "success" : "warning"}
            >{column.value(row)}</Badge
          >
        {:else}
          {column.value(row)}
        {/if}
      {/snippet}
      {#snippet footer()}
        <Pagination
          prevHref={null}
          nextHref="#datatable"
          range={m.story13Of42()}
          labels={{
            nav: m.storyDoorBuildPages(),
            previous: m.commonPrevious(),
            next: m.commonNext(),
          }}
        />
      {/snippet}
    </DataTable>
  </Variant>
  <Variant label={m.storyGroupedRowsOneGroupOpenCompactColumnMenu()}>
    <DataTable
      caption={m.storyDoorRecords()}
      {columns}
      rows={records}
      getRowId={(row) => row.id}
      hidden={["creators", "size"]}
      {visibilityHref}
      density="compact"
      expansion={{
        getSubRows: (row) => row.builds,
        open: ["smallest-5x5"],
        href: (open) => (open.length > 0 ? `?open=${open.join(",")}#datatable` : "#datatable"),
        groupLabel: (parent) => m.storyInGroup({ title: parent.title }),
      }}
      {labels}
    />
  </Variant>
  <Variant label={m.storyLoadingLabel()}>
    <DataTable
      caption={m.storyDoorBuilds()}
      captionHidden
      {columns}
      rows={[]}
      getRowId={(row) => row.id}
      loading={3}
      {labels}
    />
  </Variant>
  <Variant label={m.storyNoRows()}>
    <DataTable
      caption={m.storyDoorBuilds()}
      {columns}
      rows={[]}
      getRowId={(row) => row.id}
      {labels}
    >
      {#snippet empty()}
        <EmptyState
          title={m.storyNoBuildsMatch()}
          description={m.storyRemoveAFilterToSeeMoreBuilds()}
        />
      {/snippet}
    </DataTable>
  </Variant>
</Story>

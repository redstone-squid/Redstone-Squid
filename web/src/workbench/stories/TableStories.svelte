<script lang="ts">
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
      title: "Copper Bolt 5×5",
      size: "9 × 7 × 4",
      volume: 252,
      opening: 8,
      status: "Confirmed",
      creators: "CircuitSage, Space Builder",
    },
    {
      id: "iris",
      title: "Iris door with a much longer name than the column",
      size: "11 × 11 × 5",
      volume: 605,
      opening: 12,
      status: "Pending",
      creators: "红石猫",
    },
    {
      id: "flush",
      title: "Flush piston door",
      size: "6 × 5 × 3",
      volume: 90,
      opening: 4,
      status: "Confirmed",
      creators: "Quartz",
    },
  ];
  const columns: DataTableColumn<Build>[] = [
    { id: "title", header: "Build", value: (row) => row.title, width: "lg", truncate: true },
    { id: "status", header: "Status", value: (row) => row.status, hideable: true },
    {
      id: "size",
      header: "Size",
      value: (row) => row.size,
      mono: true,
      sortKey: "volume",
      hideable: true,
    },
    {
      id: "opening",
      header: "Opening",
      value: (row) => `${row.opening} gt`,
      numeric: true,
      sortKey: "opening",
    },
    {
      id: "creators",
      header: "Creators",
      value: (row) => row.creators,
      width: "md",
      hideable: true,
    },
  ];
  const sortHref = (key: string, direction: string) =>
    `?sort=${direction === "desc" ? "-" : ""}${key}#datatable`;
  const labels = {
    expand: "Show builds",
    collapse: "Hide builds",
    loading: "loading",
    visibility: {
      menu: "Columns",
      show: (header: string) => `Show ${header}`,
      hide: (header: string) => `Hide ${header}`,
    },
  };
  const visibilityHref = (hidden: string[]) =>
    hidden.length > 0 ? `?hide=${hidden.join(",")}#datatable` : "#datatable";

  const records: Build[] = [
    {
      id: "smallest-5x5",
      title: "Smallest 5×5 piston door",
      size: "—",
      volume: 252,
      opening: 8,
      status: "Confirmed",
      creators: "2 holders",
      builds: builds.slice(0, 2),
    },
    {
      id: "fastest-3x3",
      title: "Fastest 3×3 piston door",
      size: "—",
      volume: 90,
      opening: 4,
      status: "Confirmed",
      creators: "1 holder",
      builds: builds.slice(2),
    },
  ];
</script>

<Story
  name="DataTable"
  source="table/DataTable"
  description="The server sorts: headers are links, and aria-sort marks the sorted column. The first column stays pinned when the table scrolls."
>
  <Variant label="sorted by opening, linked rows, custom cells">
    <DataTable
      caption="Door builds"
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
          <Badge tone={row.status === "Confirmed" ? "success" : "warning"}>{row.status}</Badge>
        {:else}
          {column.value(row)}
        {/if}
      {/snippet}
      {#snippet footer()}
        <Pagination
          prevHref={null}
          nextHref="#datatable"
          range="1–3 of 42"
          labels={{ nav: "Door build pages", previous: "Previous", next: "Next" }}
        />
      {/snippet}
    </DataTable>
  </Variant>
  <Variant label="grouped rows, one group open, compact, column menu with two hidden">
    <DataTable
      caption="Door records"
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
        groupLabel: (parent) => `In ${parent.title}`,
      }}
      {labels}
    />
  </Variant>
  <Variant label="loading">
    <DataTable
      caption="Door builds"
      captionHidden
      {columns}
      rows={[]}
      getRowId={(row) => row.id}
      loading={3}
      {labels}
    />
  </Variant>
  <Variant label="no rows">
    <DataTable caption="Door builds" {columns} rows={[]} getRowId={(row) => row.id} {labels}>
      {#snippet empty()}
        <EmptyState title="No builds match" description="Remove a filter to see more builds." />
      {/snippet}
    </DataTable>
  </Variant>
</Story>

<script lang="ts" generics="Row extends RowData">
  import ArrowDown from "@lucide/svelte/icons/arrow-down";
  import ArrowUp from "@lucide/svelte/icons/arrow-up";
  import ArrowUpDown from "@lucide/svelte/icons/arrow-up-down";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import {
    columnVisibilityFeature,
    createExpandedRowModel,
    createTable,
    rowExpandingFeature,
    tableFeatures,
    type RowData,
  } from "@tanstack/svelte-table";
  import type { Snippet } from "svelte";
  import { SvelteSet } from "svelte/reactivity";
  import { cn } from "@/ui/cn";
  import Skeleton from "@/ui/feedback/Skeleton.svelte";
  import type { DataTableColumn, DataTableSort, SortDirection } from "./types";

  /**
   * A server-sorted table that works without JavaScript. Sort headers, expansion toggles and
   * `rowHref` are links the page answers; when hydrated, a toggle expands in place and
   * replaces the URL instead of navigating. The first column is the row header and stays
   * pinned while the table scrolls sideways.
   *
   * `cell` renders a cell when given; otherwise the column's `value` is shown. `hidden`
   * lists column ids to leave out, as read from the URL's `hide=`. Without rows the table
   * renders `empty` instead of a header-only table.
   */
  type Props = {
    caption: string;
    /** Keep the caption for screen readers only, when a heading above already names the table. */
    captionHidden?: boolean;
    columns: DataTableColumn<Row>[];
    rows: Row[];
    getRowId: (row: Row) => string;
    sort?: DataTableSort;
    sortHref?: (key: string, direction: SortDirection) => string;
    hidden?: string[];
    /**
     * Grouped rows. `open` holds the expanded row ids and `href` addresses another set, as the
     * URL's `open=`. `groupLabel` is hidden text read before a sub-row, such as "In Copper Bolt".
     */
    expansion?: {
      getSubRows: (row: Row) => Row[] | undefined;
      open: string[];
      href: (open: string[]) => string;
      groupLabel: (parent: Row) => string;
    };
    rowHref?: (row: Row) => string;
    cell?: Snippet<[Row, DataTableColumn<Row>]>;
    density?: "comfortable" | "compact";
    /** Skeleton rows to show while an island fetches; `labels.loading` names the busy table. */
    loading?: number;
    labels: { expand: string; collapse: string; loading?: string };
    empty?: Snippet;
    footer?: Snippet;
    class?: string;
  };
  let {
    caption,
    captionHidden = false,
    columns,
    rows,
    getRowId,
    sort = null,
    sortHref,
    hidden = [],
    expansion,
    rowHref,
    cell,
    density = "comfortable",
    loading = 0,
    labels,
    empty,
    footer,
    class: className,
  }: Props = $props();

  const id = $props.id();
  const features = tableFeatures({
    columnVisibilityFeature,
    rowExpandingFeature,
    expandedRowModel: createExpandedRowModel(),
  });
  // Seeded from the URL; a hydrated toggle changes it without a page load.
  // svelte-ignore state_referenced_locally
  const open = new SvelteSet(expansion?.open ?? []);
  const table = createTable<typeof features, Row>({
    features,
    get columns() {
      return columns.map((column) => ({
        id: column.id,
        accessorFn: column.value,
        header: column.header,
        meta: column,
      }));
    },
    get data() {
      return rows;
    },
    getRowId: (row: Row) => getRowId(row),
    getSubRows: (row: Row) => expansion?.getSubRows(row),
    state: {
      get expanded() {
        return Object.fromEntries([...open].map((rowId) => [rowId, true]));
      },
      get columnVisibility() {
        return Object.fromEntries(hidden.map((columnId) => [columnId, false]));
      },
    },
  });
  const visibleColumns = $derived(
    table.getVisibleLeafColumns().map((column) => column.columnDef.meta as DataTableColumn<Row>),
  );
  const rowModel = $derived(table.getRowModel().rows);

  const widths = { xs: "min-w-20", sm: "min-w-32", md: "min-w-48", lg: "min-w-72", auto: "" };
  const pad = $derived(density === "compact" ? "px-3 py-1.5" : "px-3 py-2.5");

  function nextOpen(rowId: string) {
    const next = new SvelteSet(open);
    if (!next.delete(rowId)) next.add(rowId);
    return [...next];
  }

  function toggle(event: MouseEvent, rowId: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || expansion === undefined) return;
    event.preventDefault();
    if (!open.delete(rowId)) open.add(rowId);
    history.replaceState(history.state, "", expansion.href([...open]));
  }

  function sortLink(key: string) {
    const direction = sort?.key === key && sort.direction === "asc" ? "desc" : "asc";
    return sortHref?.(key, direction) ?? "";
  }
</script>

{#snippet value(row: Row, column: DataTableColumn<Row>)}
  {#if cell}{@render cell(row, column)}{:else}{column.value(row) ?? ""}{/if}
{/snippet}

{#if rows.length === 0 && loading === 0}
  {@render empty?.()}
{:else}
  <div class={cn("grid min-w-0 gap-3", className)}>
    <!-- Keyboard users scroll the region, so it takes focus. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div
      role="region"
      aria-labelledby={`${id}-caption`}
      tabindex="0"
      class="min-w-0 overflow-x-auto rounded-md border border-line bg-surface"
    >
      <table
        class="w-full border-separate border-spacing-0 text-sm"
        aria-busy={loading > 0 ? "true" : undefined}
      >
        <caption
          id={`${id}-caption`}
          class={captionHidden
            ? "sr-only"
            : "border-b border-line px-3 py-2.5 text-start text-base font-semibold"}
        >
          {caption}{#if loading > 0 && labels.loading !== undefined}<span class="sr-only"
              >, {labels.loading}</span
            >{/if}
        </caption>
        <thead class="bg-surface-muted text-xs text-fg-muted">
          <tr>
            {#each visibleColumns as column, index (column.id)}
              {@const direction = sort?.key === column.sortKey ? sort?.direction : undefined}
              {@const sorted = direction !== undefined}
              <th
                scope="col"
                aria-sort={sorted ? (direction === "asc" ? "ascending" : "descending") : undefined}
                data-pinned={index === 0 ? "" : undefined}
                class={cn(
                  "border-b border-line font-semibold whitespace-nowrap",
                  column.sortKey === undefined || sortHref === undefined ? pad : "p-0",
                  column.numeric === true || column.align === "end" ? "text-end" : "text-start",
                  widths[column.width ?? "auto"],
                  index === 0 && "bg-surface-muted",
                )}
              >
                {#if column.sortKey !== undefined && sortHref !== undefined}
                  {@const Glyph = sorted
                    ? direction === "asc"
                      ? ArrowUp
                      : ArrowDown
                    : ArrowUpDown}
                  <a
                    href={sortLink(column.sortKey)}
                    class={cn(
                      "flex min-h-control-sm items-center gap-1.5 rounded-xs no-underline hover:text-fg",
                      pad,
                      sorted && "text-fg",
                      (column.numeric === true || column.align === "end") && "flex-row-reverse",
                    )}
                  >
                    {column.header}
                    <Glyph
                      class={cn("size-3.5 shrink-0", !sorted && "opacity-50")}
                      aria-hidden="true"
                    />
                  </a>
                {:else}
                  {column.header}
                {/if}
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#if loading > 0}
            {#each { length: loading }, index (index)}
              <tr>
                {#each visibleColumns as column (column.id)}
                  <td class={cn("border-b border-line", pad)}><Skeleton width="3/4" /></td>
                {/each}
              </tr>
            {/each}
          {:else}
            {#each rowModel as row (row.id)}
              {@const parent = row.getParentRow()}
              {@const subRows = row.subRows.map((sub) => `${id}-row-${sub.id}`).join(" ")}
              <tr
                id={`${id}-row-${row.id}`}
                data-depth={row.depth}
                class={cn("group/row hover:bg-surface-muted", row.depth > 0 && "bg-canvas")}
              >
                {#each visibleColumns as column, index (column.id)}
                  {#if index === 0}
                    <th
                      scope="row"
                      data-pinned
                      class={cn(
                        "border-b border-line bg-surface text-start font-semibold group-hover/row:bg-surface-muted",
                        row.depth > 0 && "bg-canvas font-normal",
                        pad,
                      )}
                    >
                      <div class={cn("flex items-center gap-2", row.depth > 0 && "ps-8")}>
                        {#if expansion !== undefined && row.subRows.length > 0}
                          {@const expanded = open.has(row.id)}
                          <a
                            href={expansion.href(nextOpen(row.id))}
                            aria-expanded={expanded}
                            aria-controls={expanded ? subRows : undefined}
                            onclick={(event) => toggle(event, row.id)}
                            class="-my-1 grid size-control-sm shrink-0 place-items-center rounded-sm text-fg-muted hover:bg-surface-raised hover:text-fg"
                          >
                            <ChevronRight
                              class={cn(
                                "size-4 transition-transform duration-150",
                                expanded && "rotate-90",
                              )}
                              aria-hidden="true"
                            />
                            <span class="sr-only">{expanded ? labels.collapse : labels.expand}</span
                            >
                          </a>
                        {/if}
                        <span class={cn("min-w-0", column.truncate === true && "truncate")}>
                          {#if parent !== undefined && expansion !== undefined}<span class="sr-only"
                              >{expansion.groupLabel(parent.original)}:
                            </span>{/if}
                          {#if rowHref !== undefined}
                            <a
                              href={rowHref(row.original)}
                              class="text-fg underline-offset-2 hover:underline"
                              >{@render value(row.original, column)}</a
                            >
                          {:else}
                            {@render value(row.original, column)}
                          {/if}
                        </span>
                      </div>
                    </th>
                  {:else}
                    <td
                      title={column.truncate === true
                        ? String(column.value(row.original) ?? "")
                        : undefined}
                      class={cn(
                        "border-b border-line",
                        pad,
                        column.numeric === true && "text-end tabular-nums",
                        column.align === "end" && "text-end",
                        column.mono === true && "font-mono text-xs",
                        column.truncate === true ? "max-w-64 truncate" : "",
                      )}
                    >
                      {@render value(row.original, column)}
                    </td>
                  {/if}
                {/each}
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>
    {@render footer?.()}
  </div>
{/if}

<style>
  /*
   * The pinned column gains a shadow once the table scrolls sideways. A scroll-driven
   * animation draws it without JavaScript; browsers without one keep the border only.
   */
  [data-pinned] {
    position: sticky;
    inset-inline-start: 0;
    z-index: 10;
    border-inline-end: 1px solid var(--color-line);
    animation: pinned-shadow linear both;
    animation-timeline: scroll(nearest inline);
    animation-range: 0 1rem;
  }
  @keyframes pinned-shadow {
    to {
      box-shadow: var(--shadow-sticky);
    }
  }
  /* The last row's border would double the region's own. */
  tbody tr:last-child > * {
    border-bottom-width: 0;
  }
</style>

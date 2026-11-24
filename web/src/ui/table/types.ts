/**
 * One `DataTable` column. `value` gives the cell's text; a table-level `cell` snippet can
 * render richer content instead. A column with `sortKey` gets a sort link in its header.
 */
export type DataTableColumn<Row> = {
  id: string;
  header: string;
  value: (row: Row) => string | number | null;
  align?: "start" | "end";
  /** Tabular figures, aligned to the end. */
  numeric?: boolean;
  mono?: boolean;
  sortKey?: string;
  width?: "xs" | "sm" | "md" | "lg" | "auto";
  /** Clip long text to one line; the full text stays in the cell's title. */
  truncate?: boolean;
};

export type SortDirection = "asc" | "desc";
export type DataTableSort = { key: string; direction: SortDirection } | null;

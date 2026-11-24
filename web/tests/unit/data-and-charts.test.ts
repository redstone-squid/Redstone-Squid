import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DistributionBar from "@/ui/chart/DistributionBar.svelte";
import RangeChart from "@/ui/chart/RangeChart.svelte";
import SeriesChart from "@/ui/chart/SeriesChart.svelte";
import Gallery from "@/ui/data/Gallery.svelte";
import DataTable from "@/ui/table/DataTable.svelte";
import type { DataTableColumn } from "@/ui/table/types";

type Row = { id: string; title: string; opening: number; children?: Row[] };
const columns: DataTableColumn<Row>[] = [
  { id: "title", header: "Build", value: (row) => row.title },
  {
    id: "opening",
    header: "Opening",
    value: (row) => row.opening,
    numeric: true,
    sortKey: "opening",
  },
];
const rows: Row[] = [
  {
    id: "doors",
    title: "Doors",
    opening: 4,
    children: [
      { id: "copper", title: "Copper Bolt", opening: 8 },
      { id: "iris", title: "Iris", opening: 12 },
    ],
  },
];
const tableLabels = { expand: "Show builds", collapse: "Hide builds" };

describe("DataTable", () => {
  it("links sort headers to the next direction and marks the sorted column", () => {
    render(DataTable<Row>, {
      caption: "Builds",
      columns,
      rows,
      getRowId: (row: Row) => row.id,
      sort: { key: "opening", direction: "asc" },
      sortHref: (key: string, direction: string) => `?sort=${key}:${direction}`,
      labels: tableLabels,
    });
    const header = screen.getByRole("columnheader", { name: "Opening" });
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(within(header).getByRole("link")).toHaveAttribute("href", "?sort=opening:desc");
    expect(screen.getByRole("columnheader", { name: "Build" })).not.toHaveAttribute("aria-sort");
    expect(screen.getByRole("region", { name: "Builds" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("rowheader", { name: /Doors/ })).toBeInTheDocument();
  });

  it("expands a group in place and keeps the URL in step", async () => {
    const user = userEvent.setup();
    const replaceState = vi.spyOn(history, "replaceState");
    render(DataTable<Row>, {
      caption: "Records",
      columns,
      rows,
      getRowId: (row: Row) => row.id,
      expansion: {
        getSubRows: (row: Row) => row.children,
        open: [],
        href: (open: string[]) => `?open=${open.join(",")}`,
        groupLabel: (parent: Row) => `In ${parent.title}`,
      },
      labels: tableLabels,
    });
    const toggle = screen.getByRole("link", { name: "Show builds" });
    expect(toggle).toHaveAttribute("href", "?open=doors");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Copper Bolt")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleName("Hide builds");
    expect(toggle).toHaveAttribute("href", "?open=");
    expect(replaceState).toHaveBeenLastCalledWith(null, "", "?open=doors");
    expect(screen.getByRole("rowheader", { name: "In Doors: Copper Bolt" })).toBeInTheDocument();
    for (const id of toggle.getAttribute("aria-controls")?.split(" ") ?? []) {
      expect(document.getElementById(id)).toBeInTheDocument();
    }
  });

  it("hides columns, and shows the empty state instead of a header-only table", () => {
    const { rerender } = render(DataTable<Row>, {
      caption: "Builds",
      columns,
      rows,
      getRowId: (row: Row) => row.id,
      hidden: ["opening"],
      labels: tableLabels,
    });
    expect(screen.queryByRole("columnheader", { name: "Opening" })).not.toBeInTheDocument();
    void rerender({ rows: [] });
    return Promise.resolve().then(() => {
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });
  });

  it("links each hideable column to the page with it hidden or shown", () => {
    const hideable = columns.map((column) => ({ ...column, hideable: column.id !== "title" }));
    render(DataTable<Row>, {
      caption: "Builds",
      columns: [...hideable, { id: "notes", header: "Notes", value: () => "", hideable: true }],
      rows,
      getRowId: (row: Row) => row.id,
      hidden: ["notes"],
      visibilityHref: (hidden: string[]) => `?hide=${hidden.join(",")}`,
      labels: {
        ...tableLabels,
        visibility: {
          menu: "Columns",
          show: (h: string) => `Show ${h}`,
          hide: (h: string) => `Hide ${h}`,
        },
      },
    });
    const toggle = screen.getByRole("button", { name: "Columns" });
    const menu = document.getElementById(toggle.getAttribute("popovertarget") ?? "");
    const links = within(menu ?? document.body).getAllByRole("link", { hidden: true });
    expect(links.map((link) => [link.textContent.trim(), link.getAttribute("href")])).toEqual([
      ["Hide Opening", "?hide=notes,opening"],
      ["Show Notes", "?hide="],
    ]);
  });

  it("marks the table busy with skeleton rows while loading", () => {
    render(DataTable<Row>, {
      caption: "Builds",
      columns,
      rows: [],
      getRowId: (row: Row) => row.id,
      loading: 2,
      labels: { ...tableLabels, loading: "loading" },
    });
    expect(screen.getByRole("table", { name: "Builds, loading" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });
});

const chartLabels = { showData: "Show data" };

describe("SeriesChart", () => {
  const series = [
    {
      id: "a",
      label: "Submitted",
      tone: 1 as const,
      points: [
        { x: "2024", y: 3, href: "/2024" },
        { x: "2025", y: 5 },
      ],
    },
    { id: "b", label: "Confirmed", tone: 2 as const, points: [{ x: "2025", y: 2 }] },
  ];

  it("ships the data as a table and links marks", () => {
    render(SeriesChart, {
      title: "Builds",
      description: "Per year",
      kind: "bar",
      x: { type: "band", label: "Year" },
      y: { label: "Builds" },
      series,
      labels: chartLabels,
    });
    const table = screen.getByRole("table", { name: "Builds" });
    expect(
      within(table)
        .getAllByRole("row")
        .map((row) => row.textContent.trim()),
    ).toEqual(["YearSubmittedConfirmed", "20243", "202552"]);
    expect(screen.getByRole("link", { name: "Submitted, 2024: 3" })).toHaveAttribute(
      "href",
      "/2024",
    );
    expect(screen.getByRole("group", { name: "Builds" })).toHaveAccessibleDescription("Per year");
  });

  it("moves between x values with the keyboard and announces them", async () => {
    const user = userEvent.setup();
    render(SeriesChart, {
      title: "Builds",
      description: "Per year",
      kind: "stacked-bar",
      x: { type: "band", label: "Year" },
      y: { label: "Builds" },
      series,
      interactive: true,
      labels: chartLabels,
    });
    const plot = screen.getByRole("application", { name: "Builds" });
    await user.tab();
    expect(plot).toHaveFocus();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("2024: Submitted 3");
    await user.keyboard("{ArrowRight}");
    expect(status).toHaveTextContent("2025: Submitted 5, Confirmed 2");
    await user.keyboard("{Home}");
    expect(status).toHaveTextContent("2024");
    await user.keyboard("{Escape}");
    expect(status).toHaveTextContent(/^$/);
  });

  it("draws step lines over time with a threshold", () => {
    const { container } = render(SeriesChart, {
      title: "Progression",
      description: "Ticks",
      kind: "step",
      x: { type: "time", label: "Date" },
      y: { label: "Ticks" },
      series: [
        {
          id: "r",
          label: "Record",
          tone: 1,
          points: [
            { x: new Date("2025-01-01T00:00:00Z"), y: 9 },
            { x: new Date("2024-01-01T00:00:00Z"), y: 12 },
          ],
        },
      ],
      annotations: [{ value: 10, label: "Instant" }],
      labels: chartLabels,
    });
    expect(screen.getByRole("img", { name: "Progression" })).toBeInTheDocument();
    expect(screen.getByText("Instant")).toBeInTheDocument();
    expect(container.querySelector("path.fill-none")?.getAttribute("d")?.match(/L/g)).toHaveLength(
      2,
    );
    expect(within(screen.getByRole("table")).getByText("2024-01-01")).toBeInTheDocument();
  });
});

describe("RangeChart", () => {
  const segments = [
    { from: 3, to: 6, value: 120, label: "Copper", href: "/copper" },
    { from: 7, to: 11, step: 2, residue: 1, value: 260, label: "Iris" },
    { from: 12, to: null, value: 400, label: "Spiral" },
  ];

  it("links segments, lists runs in the table and moves the selection by keyboard", async () => {
    const user = userEvent.setup();
    const onselect = vi.fn();
    render(RangeChart, {
      title: "Smallest door",
      description: "By width",
      segments,
      x: { label: "Width" },
      y: { label: "Volume" },
      unboundedLabel: "and wider",
      selected: 7,
      onselect,
      labels: { ...chartLabels, selected: "Width" },
    });
    expect(screen.getByRole("link", { name: "3–6: Copper, 120" })).toHaveAttribute(
      "href",
      "/copper",
    );
    expect(within(screen.getByRole("table")).getByText("12 and wider")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Width 7: Iris, 260");
    screen.getByRole("application", { name: "Smallest door" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(onselect).toHaveBeenLastCalledWith(8);
    await user.keyboard("{Home}");
    expect(onselect).toHaveBeenLastCalledWith(3);
  });
});

describe("DistributionBar", () => {
  it("ranks items and folds the rest into one bar", () => {
    render(DistributionBar, {
      title: "Blocks",
      description: "Most common",
      items: [
        { label: "Glass", value: 2 },
        { label: "Stone", value: 9, href: "/stone" },
        { label: "Dust", value: 5 },
        { label: "Lever", value: 1 },
      ],
      limit: 2,
      otherLabel: "Other",
    });
    expect(
      screen
        .getAllByRole("listitem")
        .map((item) => item.textContent.replaceAll(/\s+/g, " ").trim()),
    ).toEqual(["Stone 9", "Dust 5", "Other 3"]);
    expect(screen.getByRole("link", { name: "Stone" })).toHaveAttribute("href", "/stone");
  });
});

describe("Gallery", () => {
  const items = ["a", "b", "c"].map((name) => ({
    kind: "image" as const,
    src: `/${name}.png`,
    thumb: `/${name}-thumb.png`,
    width: 640,
    height: 400,
    alt: `Image ${name}`,
  }));
  const labels = {
    previous: "Previous",
    next: "Next",
    close: "Close",
    openItem: "View full size",
    fallback: "Missing",
    position: (current: number, total: number) => `${current} of ${total}`,
  };

  it("switches the viewer from a thumbnail link and pages the lightbox with arrow keys", async () => {
    const user = userEvent.setup();
    render(Gallery, { items, labels });
    const second = screen.getAllByRole("link")[1];
    expect(second).toHaveAttribute("href", "/b.png");
    if (second !== undefined) await user.click(second);
    expect(second).toHaveAttribute("aria-current", "true");

    await user.click(screen.getByRole("button", { name: "View full size" }));
    const dialog = await screen.findByRole("dialog", { name: "2 of 3" });
    await user.keyboard("{ArrowRight}");
    expect(dialog).toHaveAccessibleName("3 of 3");
    await user.keyboard("{ArrowRight}");
    expect(dialog).toHaveAccessibleName("1 of 3");
    await user.click(within(dialog).getByRole("button", { name: "Previous" }));
    expect(dialog).toHaveAccessibleName("3 of 3");
  });

  it("plays videos in native controls", () => {
    const { container } = render(Gallery, {
      items: items
        .slice(0, 1)
        .map((item) => ({ ...item, kind: "video" as const, src: "/clip.mp4" })),
      labels,
    });
    expect(container.querySelector("video")).toHaveAttribute("poster", "/a-thumb.png");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});

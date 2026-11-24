<script lang="ts">
  import Chart from "@/ui/chart/Chart.svelte";
  import DistributionBar from "@/ui/chart/DistributionBar.svelte";
  import RangeChart from "@/ui/chart/RangeChart.svelte";
  import SeriesChart from "@/ui/chart/SeriesChart.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const labels = { showData: "Show data" };
  const day = (date: string) => new Date(`${date}T00:00:00Z`);
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const shortDate = (date: Date) => `${months[date.getUTCMonth()] ?? ""} ${date.getUTCFullYear()}`;
  const progression = [
    { x: day("2023-02-11"), y: 18, href: "#serieschart" },
    { x: day("2023-08-02"), y: 14, href: "#serieschart" },
    { x: day("2024-03-19"), y: 11, href: "#serieschart" },
    { x: day("2025-01-05"), y: 9, href: "#serieschart" },
    { x: day("2026-06-30"), y: 8, href: "#serieschart" },
  ];
  const years = ["2021", "2022", "2023", "2024", "2025", "2026"];
  const perYear = [3, 7, 12, 9, 15, 6];
  const errors = [
    { id: "web", label: "Website", tone: 1 as const, counts: [4, 2, 6, 3, 1, 5, 2] },
    { id: "bot", label: "Discord bot", tone: 2 as const, counts: [2, 3, 1, 4, 2, 1, 3] },
    { id: "jobs", label: "Background jobs", tone: 3 as const, counts: [1, 0, 2, 1, 0, 3, 1] },
  ];
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let width = $state(13);
  const segments = [
    { from: 3, to: 6, value: 120, label: "Copper Bolt", tone: 1 as const, href: "#rangechart" },
    {
      from: 7,
      to: 15,
      step: 2,
      residue: 1,
      value: 260,
      label: "Iris door",
      tone: 2 as const,
      href: "#rangechart",
    },
    {
      from: 16,
      to: 24,
      value: 410,
      label: "Flush piston door",
      tone: 3 as const,
      href: "#rangechart",
    },
    { from: 25, to: null, value: 640, label: "Spiral door", tone: 4 as const, href: "#rangechart" },
  ];
  const blocks = [
    ["Sticky piston", 48],
    ["Redstone dust", 120],
    ["Observer", 36],
    ["Smooth stone", 210],
    ["Repeater", 24],
    ["Slime block", 30],
    ["Honey block", 12],
    ["Redstone torch", 18],
    ["Target block", 4],
    ["Comparator", 6],
    ["Glass", 9],
    ["Lever", 1],
    ["Note block", 2],
    ["Hopper", 3],
  ] as const;
</script>

<Story
  name="SeriesChart"
  source="chart/SeriesChart"
  description="Lines, steps and bars from one baseline. Every chart ships its data as a table; interactive plots add a tooltip and arrow-key navigation once hydrated."
>
  <Variant label="step, linked points, interactive">
    <SeriesChart
      title="Record progression"
      description="Opening time of the fastest 3×3 piston door, in game ticks."
      kind="step"
      x={{ type: "time", label: "Date", format: (value) => shortDate(value as Date) }}
      y={{ label: "Ticks" }}
      series={[{ id: "record", label: "Opening time", tone: 1, points: progression }]}
      annotations={[{ value: 10, label: "Instant (10 gt)" }]}
      interactive
      {labels}
    />
  </Variant>
  <Layout pattern="grid" min="card-wide">
    <Variant label="bar, small">
      <SeriesChart
        title="Builds per year"
        description="Builds CircuitSage submitted each year."
        kind="bar"
        height="sm"
        x={{ type: "band", label: "Year" }}
        y={{ label: "Builds" }}
        series={[
          {
            id: "builds",
            label: "Builds",
            tone: 1,
            points: years.map((year, index) => ({ x: year, y: perYear[index] ?? 0 })),
          },
        ]}
        {labels}
      />
    </Variant>
    <Variant label="stacked bar, interactive">
      <SeriesChart
        title="Errors per day"
        description="Error reports by surface over the last week."
        kind="stacked-bar"
        height="sm"
        x={{ type: "band", label: "Day" }}
        y={{ label: "Reports" }}
        series={errors.map((surface) => ({
          ...surface,
          points: days.map((name, index) => ({ x: name, y: surface.counts[index] ?? 0 })),
        }))}
        interactive
        {labels}
      />
    </Variant>
  </Layout>
  <Variant label="line, two series">
    <SeriesChart
      title="Submissions"
      description="Builds submitted and confirmed each month."
      kind="line"
      x={{ type: "linear", label: "Month", format: (value) => `M${String(value)}` }}
      y={{ label: "Builds" }}
      series={[
        {
          id: "submitted",
          label: "Submitted",
          tone: 1,
          points: [12, 18, 15, 22, 30, 26].map((y, index) => ({ x: index + 1, y })),
        },
        {
          id: "confirmed",
          label: "Confirmed",
          tone: 2,
          points: [8, 11, 13, 15, 21, 24].map((y, index) => ({ x: index + 1, y })),
        },
      ]}
      interactive
      {labels}
    />
  </Variant>
</Story>

<Story
  name="RangeChart"
  source="chart/RangeChart"
  description="Runs of widths, each held by one build. Odd-only runs draw only the widths that count; the last run is unbounded."
>
  <RangeChart
    title="Smallest door by width"
    description="Volume of the smallest record door at each width, in blocks."
    {segments}
    x={{ label: "Width" }}
    y={{ label: "Volume" }}
    unboundedLabel="and wider"
    selected={width}
    onselect={(next) => (width = next)}
    labels={{ ...labels, selected: "Width" }}
  />
</Story>

<Story name="DistributionBar" source="chart/DistributionBar">
  <DistributionBar
    title="Block composition"
    description="The most common blocks in the schematic."
    items={blocks.map(([label, value]) => ({ label, value }))}
    limit={8}
    otherLabel="Other blocks"
  />
</Story>

<Story name="Chart" source="chart/Chart" description="The frame, in its waiting states.">
  <Layout pattern="grid" min="card">
    {#each [["loading", "Loading the chart"], ["empty", "No records were broken this year."], ["error", "The chart could not be loaded."]] as const as [status, text] (status)}
      <Variant label={status}>
        <Chart
          title="Records broken"
          description="Per month."
          {status}
          statusText={text}
          height="sm"
        >
          {#snippet plot()}<Text>Unused</Text>{/snippet}
        </Chart>
      </Variant>
    {/each}
  </Layout>
</Story>

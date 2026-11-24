<script lang="ts">
  import * as m from "@/locales/messages-chart.gen";
  import Chart from "@/ui/chart/Chart.svelte";
  import DistributionBar from "@/ui/chart/DistributionBar.svelte";
  import RangeChart from "@/ui/chart/RangeChart.svelte";
  import SeriesChart from "@/ui/chart/SeriesChart.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const labels = { showData: m.storyShowData() };
  const day = (date: string) => new Date(`${date}T00:00:00Z`);
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
    { id: "web", label: m.storyWebsite(), tone: 1 as const, counts: [4, 2, 6, 3, 1, 5, 2] },
    { id: "bot", label: m.storyDiscordBot(), tone: 2 as const, counts: [2, 3, 1, 4, 2, 1, 3] },
    { id: "jobs", label: m.storyBackgroundJobs(), tone: 3 as const, counts: [1, 0, 2, 1, 0, 3, 1] },
  ];
  const days = [
    m.storyMon(),
    m.storyTue(),
    m.storyWed(),
    m.storyThu(),
    m.storyFri(),
    m.storySat(),
    m.storySun(),
  ];
  let width = $state(13);
  const segments = [
    {
      from: 3,
      to: 6,
      value: 120,
      label: m.storyCopperBolt(),
      tone: 1 as const,
      href: "#rangechart",
    },
    {
      from: 7,
      to: 15,
      step: 2,
      residue: 1,
      value: 260,
      label: m.storyIrisDoor(),
      tone: 2 as const,
      href: "#rangechart",
    },
    {
      from: 16,
      to: 24,
      value: 410,
      label: m.storyFlushPistonDoor(),
      tone: 3 as const,
      href: "#rangechart",
    },
    {
      from: 25,
      to: null,
      value: 640,
      label: m.storySpiralDoor(),
      tone: 4 as const,
      href: "#rangechart",
    },
  ];
  const blocks = [
    [m.storyStickyPiston(), 48],
    [m.storyRedstoneDust(), 120],
    [m.storyObserver(), 36],
    [m.storySmoothStone(), 210],
    [m.storyRepeater(), 24],
    [m.storySlimeBlock(), 30],
    [m.storyHoneyBlock(), 12],
    [m.storyRedstoneTorch(), 18],
    [m.storyTargetBlock(), 4],
    [m.storyComparator(), 6],
    [m.storyGlass(), 9],
    [m.storyLever(), 1],
    [m.storyNoteBlock(), 2],
    [m.storyHopper(), 3],
  ] as const;
</script>

<Story name="SeriesChart" source="chart/SeriesChart" description={m.storySeriesChartDescription()}>
  <Variant label={m.storyStepLinkedPointsInteractive()}>
    <SeriesChart
      title={m.storyRecordProgression()}
      description={m.storyOpeningTimeOfTheFastest33Piston()}
      kind="step"
      x={{
        type: "time",
        label: m.storyDate(),
        format: (value) => m.storyChartDate({ date: value as Date }),
      }}
      y={{ label: m.storyTicks() }}
      series={[{ id: "record", label: m.storyOpeningTime(), tone: 1, points: progression }]}
      annotations={[{ value: 10, label: m.storyInstant10Gt() }]}
      interactive
      {labels}
    />
  </Variant>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyBarSmall()}>
      <SeriesChart
        title={m.storyBuildsPerYear()}
        description={m.storyBuildsCircuitsageSubmittedEachYear()}
        kind="bar"
        height="sm"
        x={{ type: "band", label: m.storyYear() }}
        y={{ label: m.navBuilds() }}
        series={[
          {
            id: "builds",
            label: m.navBuilds(),
            tone: 1,
            points: years.map((year, index) => ({ x: year, y: perYear[index] ?? 0 })),
          },
        ]}
        {labels}
      />
    </Variant>
    <Variant label={m.storyStackedBarInteractive()}>
      <SeriesChart
        title={m.storyErrorsPerDay()}
        description={m.storyErrorReportsBySurfaceOverTheLastWeek()}
        kind="stacked-bar"
        height="sm"
        x={{ type: "band", label: m.storyDay() }}
        y={{ label: m.storyReports() }}
        series={errors.map((surface) => ({
          ...surface,
          points: days.map((name, index) => ({ x: name, y: surface.counts[index] ?? 0 })),
        }))}
        interactive
        {labels}
      />
    </Variant>
  </Layout>
  <Variant label={m.storyLineTwoSeries()}>
    <SeriesChart
      title={m.storySubmissions()}
      description={m.storyBuildsSubmittedAndConfirmedEachMonth()}
      kind="line"
      x={{
        type: "linear",
        label: m.storyMonth(),
        format: (value) => m.storyMonthNumber({ month: String(value) }),
      }}
      y={{ label: m.navBuilds() }}
      series={[
        {
          id: "submitted",
          label: m.storySubmitted(),
          tone: 1,
          points: [12, 18, 15, 22, 30, 26].map((y, index) => ({ x: index + 1, y })),
        },
        {
          id: "confirmed",
          label: m.storyConfirmed(),
          tone: 2,
          points: [8, 11, 13, 15, 21, 24].map((y, index) => ({ x: index + 1, y })),
        },
      ]}
      interactive
      {labels}
    />
  </Variant>
</Story>

<Story name="RangeChart" source="chart/RangeChart" description={m.storyRangeChartDescription()}>
  <RangeChart
    title={m.storySmallestDoorByWidth()}
    description={m.storyVolumeOfTheSmallestRecordDoorAtEach()}
    {segments}
    x={{ label: m.storyWidth() }}
    y={{ label: m.storyVolume() }}
    unboundedLabel={m.storyAndWider()}
    selected={width}
    onselect={(next) => (width = next)}
    labels={{ ...labels, selected: m.storyWidth() }}
  />
</Story>

<Story name="DistributionBar" source="chart/DistributionBar">
  <DistributionBar
    title={m.storyBlockComposition()}
    description={m.storyTheMostCommonBlocksInTheSchematic()}
    items={blocks.map(([label, value]) => ({ label, value }))}
    limit={8}
    otherLabel={m.storyOtherBlocks()}
  />
</Story>

<Story name="Chart" source="chart/Chart" description={m.storyChartDescription()}>
  <Layout pattern="grid" min="card">
    {#each [["loading", m.storyLoadingTheChart()], ["empty", m.storyNoRecordsWereBrokenThisYear()], ["error", m.storyTheChartCouldNotBeLoaded()]] as const as [status, text] (status)}
      <Variant label={status}>
        <Chart
          title={m.storyRecordsBroken()}
          description={m.storyPerMonth()}
          {status}
          statusText={text}
          height="sm"
        >
          {#snippet plot()}<Text>{m.storyUnused()}</Text>{/snippet}
        </Chart>
      </Variant>
    {/each}
  </Layout>
</Story>

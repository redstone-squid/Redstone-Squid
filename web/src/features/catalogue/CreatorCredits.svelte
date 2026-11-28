<script lang="ts">
  import type { CreatorStats, PublicBuildSummary } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import SeriesChart from "@/ui/chart/SeriesChart.svelte";
  import List from "@/ui/data/List.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import FieldGroup from "@/ui/forms/FieldGroup.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Pagination from "@/ui/navigation/Pagination.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import BuildRow from "./BuildRow.svelte";
  import RecordRow from "./RecordRow.svelte";
  import { buildName, creditRoleLabel } from "./presentation";

  /**
   * A creator's page body: their counts, builds per year, one page of builds, and the
   * records they hold. With `claimNameId`, a signed-in visitor can claim this name's credits
   * on the listed builds; the form posts to the current page.
   */
  type Props = {
    claimNameId?: number;
    stats: CreatorStats;
    builds: PublicBuildSummary[];
    prevHref: string | null;
    nextHref: string | null;
  };
  let { stats, builds, prevHref, nextHref, claimNameId }: Props = $props();
  const years = $derived(stats.builds_by_year.toSorted((a, b) => a.year - b.year));
  const claimable = $derived(
    claimNameId === undefined
      ? []
      : builds.flatMap((build) =>
          (build.credits ?? [])
            .filter((credit) => credit.credit_id != null && credit.name_id === claimNameId)
            .map((credit) => ({ build, credit, id: credit.credit_id ?? "" })),
        ),
  );
</script>

<Layout pattern="stack" gap="xl">
  <StatGroup
    items={[
      { label: m.navBuilds(), value: String(stats.build_count) },
      { label: m.creatorRecordsHeld(), value: String(stats.records_held.length) },
      { label: m.creatorRecordsEver(), value: String(stats.records_ever) },
    ]}
  />
  <Section title={m.navBuilds()}>
    {#if builds.length === 0}
      <EmptyState title={m.creatorBuildsEmpty()} />
    {:else}
      <Layout pattern="stack">
        {#if years.length > 1}
          <SeriesChart
            title={m.creatorBuildsPerYear()}
            description={m.creatorBuildsPerYearDescription()}
            kind="bar"
            height="sm"
            x={{ type: "band", label: m.creatorYear() }}
            y={{
              label: m.navBuilds(),
              // Counts are whole; hide the axis's in-between ticks.
              format: (count) => (Number.isInteger(count) ? String(count) : ""),
            }}
            series={[
              {
                id: "builds",
                label: m.navBuilds(),
                tone: 1,
                points: years.map(({ year, count }) => ({ x: String(year), y: count })),
              },
            ]}
            labels={{ showData: m.catalogueShowData() }}
          />
        {/if}
        <List>
          {#each builds as build (build.id)}
            <BuildRow {build} />
          {/each}
        </List>
        <Pagination
          {prevHref}
          {nextHref}
          labels={{ nav: m.navPagination(), previous: m.commonPrevious(), next: m.commonNext() }}
        />
      </Layout>
    {/if}
  </Section>
  <Section title={m.creatorRecordsHeld()}>
    {#if stats.records_held.length === 0}
      <EmptyState title={m.creatorRecordsEmpty({ ever: stats.records_ever })} />
    {:else}
      <List>
        {#each stats.records_held as record (record.id)}
          <RecordRow record={{ record_id: record.id, title: record.title }} />
        {/each}
      </List>
    {/if}
  </Section>
  {#if claimable.length > 0}
    <Section title={m.creatorClaimTitle()} description={m.creatorClaimDescription()} surface>
      <form method="post">
        <Layout pattern="stack" gap="md">
          <FieldGroup label={m.creatorClaimSelect()} labelVisibility="hidden">
            {#each claimable as { build, credit, id } (id)}
              <CheckField
                name="credit"
                value={id}
                label={buildName(build)}
                hint={creditRoleLabel(credit.role)}
              />
            {/each}
          </FieldGroup>
          <div><Button type="submit" variant="primary">{m.creatorClaimSubmit()}</Button></div>
        </Layout>
      </form>
    </Section>
  {/if}
</Layout>

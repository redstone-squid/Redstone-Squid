<script lang="ts">
  import { groupBy } from "es-toolkit";
  import type { PublicRecordDetail, RecordCriterion } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import {
    buildKindLabel,
    recordCriterionLabel,
    recordFactLabel,
    versionScopeLabel,
  } from "@/locales/vocabulary.gen";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import List from "@/ui/data/List.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Tabs from "@/ui/navigation/Tabs.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import BuildCard from "./BuildCard.svelte";
  import BuildRow from "./BuildRow.svelte";
  import FeaturedBuild from "./FeaturedBuild.svelte";
  import HolderTimeline from "./HolderTimeline.svelte";
  import { recordHref, recordMeasurements } from "./presentation";

  /**
   * A record's page: the number that decides it and its holder, the builds tied with it,
   * the builds that could take it once their missing facts are known, and who held it
   * before. A shared record is explained, either by identical completion dates or by dates
   * nobody has supplied yet. A retired record has no competing builds and shows its history.
   * Other criteria for the same category appear as tabs under the title.
   */
  let { record }: { record: PublicRecordDetail } = $props();
  const kind = $derived(buildKindLabel(record.build_kind));
  const gapsByBuild = $derived(groupBy(record.gaps ?? [], (gap) => gap.build_id));
  const missing = (buildId: number): string[] =>
    (gapsByBuild[buildId] ?? []).map((gap) => recordFactLabel(gap.field));
  const measurements = $derived(recordMeasurements(record));
  const shared = $derived(record.holder_builds.length > 1);
  const sharedUndated = $derived(
    shared && record.holder_builds.some((build) => missing(build.id).length > 0),
  );
  const contenders = $derived(record.contender_builds ?? []);
  const tied = $derived(record.tied_builds ?? []);
  const retiredAt = $derived(record.retired_at ?? null);
  const history = $derived(record.history ?? []);
  const criteria: RecordCriterion[] = [
    "first",
    "fastest",
    "smallest",
    "fastest_smallest",
    "smallest_fastest",
  ];
  const tabLabel = (criterion: RecordCriterion, scope: PublicRecordDetail["version_scope"]) =>
    scope === "up_to_date"
      ? m.recordSiblingLabel({
          criterion: recordCriterionLabel(criterion),
          scope: versionScopeLabel(scope),
        })
      : recordCriterionLabel(criterion);
  const siblings = $derived(
    [
      { ...record, retired: retiredAt !== null, current: true },
      ...(record.siblings ?? []).map((sibling) => ({ ...sibling, current: false })),
    ]
      .toSorted((a, b) => {
        const byCriterion = criteria.indexOf(a.criterion) - criteria.indexOf(b.criterion);
        const current = (scope: string) => Number(scope === "up_to_date");
        return byCriterion === 0
          ? current(a.version_scope) - current(b.version_scope)
          : byCriterion;
      })
      .map((sibling) => ({
        label: tabLabel(sibling.criterion, sibling.version_scope),
        href: recordHref(sibling.record_id),
        current: sibling.current,
      })),
  );
</script>

<Layout pattern="stack" gap="xl">
  <PageHeader
    title={record.title}
    eyebrow={recordCriterionLabel(record.criterion)}
    description={record.subtitle ?? undefined}
  >
    {#snippet meta()}
      <Badge tone={retiredAt === null ? "success" : "neutral"} dot>
        {retiredAt === null ? m.recordCurrent() : m.recordRetired({ date: retiredAt })}
      </Badge>
      {#if record.refresh_pending === true}
        <Badge tone="info" dot>{m.recordRefreshPending()}</Badge>
      {/if}
      <MetaList items={[kind, versionScopeLabel(record.version_scope)]} />
      <Text as="span" size="sm" tone="muted">
        {record.last_checked_at != null
          ? m.recordLastChecked({ date: record.last_checked_at })
          : m.recordComputed({ date: record.computed_at })}
      </Text>
    {/snippet}
    {#snippet notice()}
      {#if retiredAt !== null}
        <Notice variant="banner" title={m.recordRetiredDescription()} />
      {:else if contenders.length > 0}
        <Notice
          variant="banner"
          tone="warning"
          title={m.recordChangeable({ count: contenders.length })}
        />
      {/if}
    {/snippet}
  </PageHeader>
  {#if siblings.length > 1}
    <Tabs label={m.recordSiblings()} items={siblings} />
  {/if}
  {#if retiredAt === null}
    {#if shared}
      <Section
        title={m.recordCoHolders()}
        description={sharedUndated ? m.recordCoHoldersUndated() : m.recordCoHoldersDescription()}
      >
        <Layout pattern="stack" gap="lg">
          <StatGroup layout="inline" size="lg" lead items={measurements} />
          <Layout as="ul" pattern="grid">
            {#each record.holder_builds as build, index (build.id)}
              <BuildCard
                {build}
                category={kind}
                as="li"
                priority={index === 0}
                missingFacts={missing(build.id)}
              />
            {/each}
          </Layout>
        </Layout>
      </Section>
    {:else if record.holder_builds[0]}
      <FeaturedBuild
        build={record.holder_builds[0]}
        eyebrow={m.recordHolders()}
        {measurements}
        since={record.holder_since ?? null}
      />
    {:else}
      <EmptyState title={m.recordNoHolders()} />
    {/if}
  {/if}
  {#if tied.length > 0}
    <Section title={m.recordTied()} description={m.recordTiedDescription()}>
      <List>
        {#each tied as build (build.id)}
          <BuildRow {build} />
        {/each}
      </List>
    </Section>
  {/if}
  {#if contenders.length > 0}
    <Section title={m.recordContenders()} description={m.recordContendersDescription()}>
      <List>
        {#each contenders as build (build.id)}
          <BuildRow {build} missingFacts={missing(build.id)} />
        {/each}
      </List>
    </Section>
  {/if}
  {#if history.length > 0}
    <Section title={m.recordHistory()}>
      <HolderTimeline {history} criterion={record.criterion} label={m.recordHistory()} />
    </Section>
  {/if}
</Layout>

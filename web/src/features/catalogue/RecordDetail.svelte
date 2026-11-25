<script lang="ts">
  import { groupBy } from "es-toolkit";
  import type { PublicRecordDetail } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import {
    buildKindLabel,
    recordCriterionLabel,
    recordFactLabel,
    resolutionStatusLabel,
    versionScopeLabel,
  } from "@/locales/vocabulary.gen";
  import Text from "@/ui/content/Text.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import BuildCard from "./BuildCard.svelte";

  /**
   * A record with its holder, the builds that could take it once their missing facts are
   * known, and the builds tied with it. A record usually has one holder; a shared record is
   * explained, either by identical completion dates or by dates nobody has supplied yet.
   */
  let { record }: { record: PublicRecordDetail } = $props();
  const kind = $derived(buildKindLabel(record.build_kind));
  const gapsByBuild = $derived(groupBy(record.gaps ?? [], (gap) => gap.build_id));
  const missing = (buildId: number): string[] =>
    (gapsByBuild[buildId] ?? []).map((gap) => recordFactLabel(gap.field));
  const shared = $derived(record.holder_builds.length > 1);
  const sharedUndated = $derived(
    shared && record.holder_builds.some((build) => missing(build.id).length > 0),
  );
  const contenders = $derived(record.contender_builds ?? []);
  const tied = $derived(record.tied_builds ?? []);
</script>

<Layout pattern="stack" gap="xl">
  <PageHeader
    title={record.title}
    eyebrow={recordCriterionLabel(record.criterion)}
    description={record.subtitle ?? undefined}
  >
    {#snippet meta()}
      <Badge tone={record.status === "resolved" ? "success" : "warning"} dot>
        {m.recordStatus({ status: resolutionStatusLabel(record.status) })}
      </Badge>
      <Text as="span" size="sm" tone="muted">
        {record.last_checked_at != null
          ? m.recordLastChecked({ date: record.last_checked_at })
          : m.recordComputed({ date: record.computed_at })}
      </Text>
    {/snippet}
    {#snippet notice()}
      {#if record.refresh_pending === true}
        <Notice variant="banner" title={m.recordRefreshPending()} />
      {/if}
      {#if contenders.length > 0}
        <Notice
          variant="banner"
          tone="warning"
          title={m.recordChangeable({ count: contenders.length })}
        />
      {/if}
    {/snippet}
  </PageHeader>
  <DescriptionList
    layout="inline"
    items={[
      { term: m.recordBuildKind(), value: kind },
      { term: m.recordVersionScope(), value: versionScopeLabel(record.version_scope) },
    ]}
  />
  <Section
    title={shared ? m.recordCoHolders() : m.recordHolders()}
    description={shared
      ? sharedUndated
        ? m.recordCoHoldersUndated()
        : m.recordCoHoldersDescription()
      : undefined}
  >
    {#if record.holder_builds.length === 0}
      <EmptyState title={m.recordNoHolders()} />
    {:else}
      <Layout as="ul" pattern="grid">
        {#each record.holder_builds as build, index (build.id)}
          <BuildCard
            {build}
            category={kind}
            as="li"
            priority={index === 0}
            missingFacts={shared ? missing(build.id) : []}
          />
        {/each}
      </Layout>
    {/if}
  </Section>
  {#if contenders.length > 0}
    <Section title={m.recordContenders()} description={m.recordContendersDescription()}>
      <Layout as="ul" pattern="grid">
        {#each contenders as build (build.id)}
          <BuildCard {build} category={kind} as="li" missingFacts={missing(build.id)} />
        {/each}
      </Layout>
    </Section>
  {/if}
  {#if tied.length > 0}
    <Section title={m.recordTied()} description={m.recordTiedDescription()}>
      <Layout as="ul" pattern="grid">
        {#each tied as build (build.id)}
          <BuildCard {build} category={kind} as="li" />
        {/each}
      </Layout>
    </Section>
  {/if}
</Layout>

<script lang="ts">
  import type { PublicRecordDetail } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import {
    buildKindLabel,
    recordCriterionLabel,
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

  /** A record with its classification, freshness, and holders in the API's order. */
  let { record }: { record: PublicRecordDetail } = $props();
  const kind = $derived(buildKindLabel(record.build_kind));
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
    {/snippet}
  </PageHeader>
  <DescriptionList
    layout="inline"
    items={[
      { term: m.recordBuildKind(), value: kind },
      { term: m.recordVersionScope(), value: versionScopeLabel(record.version_scope) },
    ]}
  />
  <Section title={m.recordHolders()}>
    {#if record.holder_builds.length === 0}
      <EmptyState title={m.recordNoHolders()} />
    {:else}
      <Layout as="ul" pattern="grid">
        {#each record.holder_builds as build, index (build.id)}
          <BuildCard {build} category={kind} as="li" eager={index === 0} />
        {/each}
      </Layout>
    {/if}
  </Section>
</Layout>

<script lang="ts">
  import type { MetricDelta, PredictedRecord, RecordPreview } from "@/generated/types.gen";
  import { formatDateTime, formatList } from "@/locales/format";
  import { getLocale } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import { recordFactLabel, versionScopeLabel } from "@/locales/vocabulary.gen";
  import Heading from "@/ui/content/Heading.svelte";
  import Link from "@/ui/content/Link.svelte";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import { buildHref, metricLabel, metricValue, recordHref } from "./presentation";

  /**
   * How a submission would change the records, compared with the builds approved today. Each
   * category says whether the build would hold, share, tie, or lose it, how its numbers
   * compare with the current holder's, and which facts are still needed to decide.
   */
  let { preview }: { preview: RecordPreview } = $props();
  const outcomes = {
    holds: { label: m.predictionHolds, tone: "success" },
    shares: { label: m.predictionShares, tone: "info" },
    ties_later: { label: m.predictionTies, tone: "neutral" },
    loses: { label: m.predictionLoses, tone: "danger" },
    undecided: { label: m.predictionUndecided, tone: "warning" },
  } as const satisfies Record<PredictedRecord["outcome"], unknown>;
  // Every metric a record compares is better lower: fewer ticks, fewer blocks, an earlier date.
  const comparison = (delta: MetricDelta) => {
    const amount = metricValue(Math.abs(delta.difference), delta.unit);
    if (delta.difference < 0)
      return { text: m.predictionBetter({ amount }), tone: "success" } as const;
    if (delta.difference > 0)
      return { text: m.predictionWorse({ amount }), tone: "danger" } as const;
    return { text: m.predictionEqual(), tone: "neutral" } as const;
  };
  const evaluation = $derived([
    ...(preview.revision === null
      ? []
      : [m.submissionReviewedVersion({ revision: preview.revision })]),
    formatDateTime(getLocale(), preview.evaluated_at, { dateStyle: "medium", timeStyle: "short" }),
  ]);
</script>

<Layout pattern="stack" gap="xl">
  <PageHeader title={m.predictionTitle()} description={m.predictionExplanation()}>
    {#snippet meta()}<MetaList items={evaluation} />{/snippet}
    {#snippet notice()}
      {#if preview.missing_fields.length > 0}
        <Notice
          variant="banner"
          tone="warning"
          title={m.predictionMissingFields({
            fields: formatList(preview.missing_fields.map(metricLabel)),
          })}
        />
      {/if}
    {/snippet}
  </PageHeader>
  {#if preview.records.length === 0}
    {#if preview.missing_fields.length === 0}<EmptyState title={m.predictionNoCategories()} />{/if}
  {:else}
    <Layout as="ul" pattern="stack" gap="md">
      {#each preview.records as row (row.category_key)}
        {@const outcome = outcomes[row.outcome]}
        <Card as="li">
          <Layout pattern="split" align="start">
            <Layout pattern="stack" gap="2xs">
              <Heading level={2} size="md">
                {#if row.record_id !== null}
                  <Link href={recordHref(row.record_id)} variant="standalone">{row.title}</Link>
                {:else}{row.title}{/if}
              </Heading>
              <Text size="sm" tone="muted">{versionScopeLabel(row.scope)}</Text>
            </Layout>
            <Badge tone={outcome.tone} size="md" dot>{outcome.label()}</Badge>
          </Layout>
          {#if row.outcome === "ties_later"}
            <Text size="sm" tone="muted">{m.predictionTiesDescription()}</Text>
          {/if}
          {#each row.current_holders as holder (holder.build_id)}
            <Layout pattern="stack" gap="xs">
              <Text size="sm">
                {m.predictionCurrentHolder()}
                <Link href={buildHref(holder.build_id)}>
                  {holder.title ?? m.submissionBuildNumber({ id: holder.build_id })}
                </Link>
              </Text>
              {#each holder.deltas as delta (delta.metric)}
                {@const result = comparison(delta)}
                <Layout pattern="cluster" gap="sm" align="baseline">
                  <Text as="span" size="sm" tone="muted">{metricLabel(delta.metric)}</Text>
                  <Text as="span" weight="semibold" numeric>
                    {m.predictionDelta({
                      holder: metricValue(delta.holder, delta.unit),
                      candidate: metricValue(delta.candidate, delta.unit),
                    })}
                  </Text>
                  <Badge tone={result.tone}>{result.text}</Badge>
                </Layout>
              {/each}
            </Layout>
          {/each}
          {#each row.missing as gap (`${gap.fact}-${String(gap.build_id)}`)}
            <Text size="sm" tone="warning">
              {gap.build_id === null
                ? m.predictionMissingThis({ fact: recordFactLabel(gap.fact) })
                : m.predictionMissingOther({
                    fact: recordFactLabel(gap.fact),
                    build: m.submissionBuildNumber({ id: gap.build_id }),
                  })}
            </Text>
          {/each}
        </Card>
      {/each}
    </Layout>
  {/if}
</Layout>

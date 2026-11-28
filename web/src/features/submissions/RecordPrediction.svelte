<script lang="ts">
  import type { RecordPreview } from "@/generated/types.gen";
  import { formatDateTime } from "@/locales/format";
  import { getLocale, localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Link from "@/ui/content/Link.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";

  /**
   * How a submission would change the records, compared with the builds approved today. Each
   * category says whether the build would hold, share, tie, or lose it, and which facts are
   * still needed to decide.
   */
  let { preview }: { preview: RecordPreview } = $props();
  const outcomes = {
    holds: m.predictionHolds,
    shares: m.predictionShares,
    ties_later: m.predictionTies,
    loses: m.predictionLoses,
    undecided: m.predictionUndecided,
  } satisfies Record<RecordPreview["records"][number]["outcome"], unknown>;
  const words = (names: string[]) => names.map((name) => name.replaceAll("_", " ")).join(", ");
</script>

<PageHeader title={m.predictionTitle()} description={m.predictionExplanation()} />
{#if preview.revision !== null}
  <p>{m.submissionReviewedVersion({ revision: preview.revision })}</p>
{/if}
<time datetime={preview.evaluated_at}>
  {formatDateTime(getLocale(), preview.evaluated_at, { dateStyle: "medium", timeStyle: "short" })}
</time>
{#if preview.missing_fields.length > 0}
  <p>{m.predictionMissing()} {preview.missing_fields.join(", ")}</p>
{/if}
{#if preview.records.length === 0 && preview.missing_fields.length === 0}
  <p>{m.predictionNoCategories()}</p>
{/if}
{#each preview.records as row (row.category_key)}
  <Section title={row.title}>
    <p>
      {outcomes[row.outcome]()} · {row.scope === "all_time"
        ? m.predictionAllTime()
        : m.predictionCurrent()}
    </p>
    {#each row.missing as gap (`${gap.fact}-${String(gap.build_id)}`)}
      {@const build = gap.build_id === null ? [] : [m.submissionBuildNumber({ id: gap.build_id })]}
      <p>{[m.predictionMissing(), words(gap.fields), ...build].join(" ")}</p>
    {/each}
    {#if row.current_holders.length > 0}
      <ul>
        {#each row.current_holders as holder (holder.build_id)}
          <li>
            {m.predictionCurrentHolder()}
            <Link href={localizePath(`/builds/${holder.build_id}`)}>
              {holder.title ?? m.submissionBuildNumber({ id: holder.build_id })}
            </Link>
            {#if holder.deltas.length > 0}
              <ul>
                {#each holder.deltas as delta (delta.metric)}
                  <li>
                    {delta.metric.replaceAll("_", " ")} · {delta.holder} → {delta.candidate}
                    ({delta.difference > 0 ? "+" : ""}{delta.difference}
                    {delta.unit})
                  </li>
                {/each}
              </ul>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </Section>
{/each}

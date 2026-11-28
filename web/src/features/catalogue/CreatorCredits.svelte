<script lang="ts">
  import type { CreatorStats, PublicBuildSummary } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Pagination from "@/ui/navigation/Pagination.svelte";

  type Props = {
    claimNameId?: number;
    stats: CreatorStats;
    builds: PublicBuildSummary[];
    prevHref: string | null;
    nextHref: string | null;
  };
  let { stats, builds, prevHref, nextHref, claimNameId }: Props = $props();
</script>

<Layout pattern="stack">
  <section>
    <h2>{m.creatorBuildCount({ count: stats.build_count })}</h2>
    {#if stats.builds_by_year.length > 0}
      <dl>
        {#each stats.builds_by_year as entry (entry.year)}
          <div>
            <dt>{entry.year}</dt>
            <dd>{entry.count}</dd>
          </div>
        {/each}
      </dl>
    {/if}
    <ul>
      {#each builds as build (build.id)}
        <li>
          <a href={localizePath(`/builds/${build.id}`)}>{build.display_name ?? build.title}</a>
          <ul aria-label={m.buildCreators()}>
            {#each build.credits ?? [] as creator (creator.credit_id)}
              <li>
                <a
                  href={localizePath(
                    creator.account_id !== null
                      ? `/accounts/${creator.account_id}`
                      : `/creators/${encodeURIComponent(creator.name)}`,
                  )}>{creator.name}</a
                >
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ul>
    <Pagination
      {prevHref}
      {nextHref}
      labels={{ nav: m.navPagination(), previous: m.commonPrevious(), next: m.commonNext() }}
    />
  </section>
  <section>
    <h2>{m.recordCurrent()} · {m.navRecords()}</h2>
    <p>{m.creatorRecordCounts({ held: stats.records_held.length, ever: stats.records_ever })}</p>
    {#if stats.records_held.length > 0}
      <ul>
        {#each stats.records_held as record (record.id)}
          <li><a href={localizePath(`/records/${record.id}`)}>{record.title}</a></li>
        {/each}
      </ul>
    {/if}
  </section>
  {#if claimNameId !== undefined && builds.length > 0}
    <form method="post">
      <fieldset>
        <legend>{m.creatorClaimSelect()}</legend>
        {#each builds as build (build.id)}
          {#each build.credits ?? [] as credit (credit.credit_id)}
            {#if credit.credit_id != null && credit.name_id === claimNameId}
              <CheckField
                name="credit"
                value={credit.credit_id}
                label={`${build.display_name ?? build.title} · ${credit.name} (${credit.role})`}
              />
            {/if}
          {/each}
        {/each}
      </fieldset>
      <Button type="submit">{m.creatorClaimSubmit()}</Button>
    </form>
  {/if}
</Layout>

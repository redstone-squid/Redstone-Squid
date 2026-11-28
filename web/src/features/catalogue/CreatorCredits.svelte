<script lang="ts">
  import type { CreatorStats, PublicBuildSummary } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import Link from "@/ui/content/Link.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import List from "@/ui/data/List.svelte";
  import ListItem from "@/ui/data/ListItem.svelte";
  import FieldGroup from "@/ui/forms/FieldGroup.svelte";
  import Section from "@/ui/patterns/Section.svelte";
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
  <Section title={m.creatorBuildCount({ count: stats.build_count })}>
    {#if stats.builds_by_year.length > 0}
      <DescriptionList
        items={stats.builds_by_year.map(({ year, count }) => ({
          term: String(year),
          value: String(count),
          numeric: true,
        }))}
      />
    {/if}
    <ul class="pagination-results">
      {#each builds as build (build.id)}
        <li>
          <Link href={localizePath(`/builds/${build.id}`)}>{build.display_name ?? build.title}</Link
          >
          <ul aria-label={m.buildCreators()}>
            {#each build.credits ?? [] as creator (creator.credit_id)}
              <li>
                <Link
                  href={localizePath(
                    creator.account_id !== null
                      ? `/accounts/${creator.account_id}`
                      : `/creators/${encodeURIComponent(creator.name)}`,
                  )}>{creator.name}</Link
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
  </Section>
  <Section title={`${m.recordCurrent()} · ${m.navRecords()}`}>
    <p>{m.creatorRecordCounts({ held: stats.records_held.length, ever: stats.records_ever })}</p>
    {#if stats.records_held.length > 0}
      <List>
        {#each stats.records_held as record (record.id)}
          <ListItem
            density="compact"
            href={localizePath(`/records/${record.id}`)}
            title={record.title}
          />
        {/each}
      </List>
    {/if}
  </Section>
  {#if claimNameId !== undefined && builds.length > 0}
    <form method="post">
      <FieldGroup label={m.creatorClaimSelect()}>
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
      </FieldGroup>
      <Button type="submit">{m.creatorClaimSubmit()}</Button>
    </form>
  {/if}
</Layout>

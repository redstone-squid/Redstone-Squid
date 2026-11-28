<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import Heading from "@/ui/content/Heading.svelte";
  import Link from "@/ui/content/Link.svelte";
  import Text from "@/ui/content/Text.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import BuildPreview from "./BuildPreview.svelte";
  import CreatorLinks from "./CreatorLinks.svelte";
  import { buildHref, buildName, buildSize, ticks, type Measurement } from "./presentation";

  /**
   * One build shown large: a record's only holder, or a build picked for the home page.
   * `eyebrow` says why it is featured, such as "Record holder". `measurements` are the
   * numbers it is featured for, shown large; `since` is when it took the record. Without a
   * preview the text stands alone rather than beside an empty frame.
   */
  type Props = {
    build: PublicBuildSummary;
    eyebrow?: string;
    measurements?: Measurement[];
    since?: string | null;
  };
  let { build, eyebrow, measurements = [], since = null }: Props = $props();
</script>

<article class="@container">
  <Layout
    pattern="grid"
    class={build.preview === null ? undefined : "@2xl:grid-cols-2"}
    min="card-wide"
    align="center"
    gap="lg"
  >
    {#if build.preview !== null}
      <BuildPreview {build} priority class="rounded-md border border-line" />
    {/if}
    <Layout pattern="stack" gap="md">
      <Layout pattern="stack" gap="xs">
        <Heading level={2} {eyebrow}>
          <Link href={buildHref(build.id)} variant="standalone">{buildName(build)}</Link>
        </Heading>
        {#if build.creators.length > 0}<CreatorLinks creators={build.creators} />{/if}
      </Layout>
      {#if measurements.length > 0}
        <StatGroup layout="inline" size="lg" lead items={measurements} />
      {/if}
      <DescriptionList
        layout="inline"
        size="sm"
        items={[
          { term: m.buildDimensions(), value: buildSize(build.dimensions), numeric: true },
          { term: m.buildOpening(), value: ticks(build.opening_time), numeric: true },
          { term: m.buildClosing(), value: ticks(build.closing_time), numeric: true },
        ]}
      />
      {#if since !== null}
        <Text size="sm" tone="muted">{m.recordSince({ date: since })}</Text>
      {/if}
    </Layout>
  </Layout>
</article>

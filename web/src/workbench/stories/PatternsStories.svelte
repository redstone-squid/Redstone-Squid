<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Pencil from "@lucide/svelte/icons/pencil";
  import BuildCard from "@/features/catalogue/BuildCard.svelte";
  import RecordCard from "@/features/catalogue/RecordCard.svelte";
  import Button from "@/ui/actions/Button.svelte";
  import Avatar from "@/ui/data/Avatar.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import { buildFixtures, recordFixtures } from "@/workbench/fixtures";
  import Story from "./Story.svelte";

  const categories = [m.trialDoor(), m.storySeamlessDoor(), m.storyExtender()];
</script>

<Story name="PageHeader" source="patterns/PageHeader" description={m.storyPageHeaderDescription()}>
  <PageHeader
    title={m.storyCopperBolt55()}
    eyebrow={m.trialDoor()}
    description={m.storyAFastFlush55Door()}
    breadcrumbs={{
      label: m.storyBreadcrumb(),
      items: [{ label: m.navBuilds(), href: "#pageheader" }, { label: m.storyCopperBolt55() }],
    }}
  >
    {#snippet media()}<Avatar name="CircuitSage" size="lg" />{/snippet}
    {#snippet meta()}<Badge tone="success" dot>{m.storyApproved()}</Badge>{/snippet}
    {#snippet actions()}<Button icon={Pencil}>{m.storyEdit()}</Button>{/snippet}
    {#snippet notice()}<Notice tone="warning">{m.storyPendingReviewByStaff()}</Notice>{/snippet}
  </PageHeader>
</Story>

<Story name="Section" source="patterns/Section">
  <Section title={m.recordHolders()} description={m.storyBuildsThatHoldThisRecord()}>
    {#snippet actions()}<Button size="sm" variant="quiet">{m.storyViewAll()}</Button>{/snippet}
    <p class="text-sm">{m.storySectionContent()}</p>
  </Section>
  <Section title={m.storySurfaceSection()} surface level={3}>
    <p class="text-sm">{m.storyDrawnAsAPanel()}</p>
  </Section>
</Story>

<Story
  name="Catalogue cards"
  heading={m.storyCatalogueCards()}
  source="features/catalogue"
  description={m.storyCatalogueCardsDescription()}
>
  <Layout as="ul" pattern="grid">
    {#each buildFixtures() as build, index (build.id)}
      <BuildCard {build} category={categories[index] ?? ""} as="li" />
    {/each}
  </Layout>
  <Layout as="ul" pattern="grid" min="card-wide">
    {#each recordFixtures() as record (record.record_id)}
      <RecordCard {record} as="li" />
    {/each}
  </Layout>
</Story>

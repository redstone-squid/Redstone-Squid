<script lang="ts">
  import * as m from "@/locales/messages-patterns.gen";
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
  import SplitLayout from "@/ui/patterns/SplitLayout.svelte";
  import Variant from "./Variant.svelte";
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

{#snippet sideNav()}
  <ul class="grid gap-2 text-sm">
    <li><a href="#splitlayout">General</a></li>
    <li><a href="#splitlayout">Media</a></li>
    <li><a href="#splitlayout">Credits</a></li>
  </ul>
{/snippet}

<Story
  name="SplitLayout"
  source="patterns/SplitLayout"
  description="A sidebar beside the content, sized by its own width. Narrow it to see the collapse."
>
  <Variant label="stack, sidebar at the start">
    <SplitLayout sidebarLabel="Sections" sidebar={sideNav}>
      <p class="text-sm">Main content flows beside the sidebar when there is room.</p>
    </SplitLayout>
  </Variant>
  <Variant label="sheet, sidebar at the end, sticky">
    <SplitLayout
      sidebarLabel="Filters"
      triggerLabel="Filters · 2"
      closeLabel="Close filters"
      collapse="sheet"
      side="end"
      sticky
      sidebar={sideNav}
    >
      <p class="text-sm">On a narrow layout the sidebar opens as a sheet from the end edge.</p>
    </SplitLayout>
  </Variant>
  <Variant label="sheet in a narrow frame">
    <div class="max-w-dialog-sm">
      <SplitLayout
        sidebarLabel="Account"
        closeLabel="Close account navigation"
        collapse="sheet"
        sidebar={sideNav}
      >
        <p class="text-sm">This frame is always narrow, so the sidebar stays behind a button.</p>
      </SplitLayout>
    </div>
  </Variant>
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

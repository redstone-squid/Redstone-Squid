<script lang="ts">
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

  const categories = ["Piston door", "Seamless door", "Extender"];
</script>

<Story
  name="PageHeader"
  source="patterns/PageHeader"
  description="Breadcrumbs, the page's h1, media, meta, actions, and a notice."
>
  <PageHeader
    title="Copper Bolt 5×5"
    eyebrow="Piston door"
    description="A fast flush 5×5 door."
    breadcrumbs={{
      label: "Breadcrumb",
      items: [{ label: "Builds", href: "#pageheader" }, { label: "Copper Bolt 5×5" }],
    }}
  >
    {#snippet media()}<Avatar name="CircuitSage" size="lg" />{/snippet}
    {#snippet meta()}<Badge tone="success" dot>Approved</Badge><Badge>Java 1.21</Badge>{/snippet}
    {#snippet actions()}<Button icon={Pencil}>Edit</Button>{/snippet}
    {#snippet notice()}<Notice tone="warning">Pending review by staff.</Notice>{/snippet}
  </PageHeader>
</Story>

<Story name="Section" source="patterns/Section">
  <Section title="Holders" description="Builds that hold this record.">
    {#snippet actions()}<Button size="sm" variant="quiet">View all</Button>{/snippet}
    <p class="text-sm">Section content.</p>
  </Section>
  <Section title="Surface section" surface level={3}>
    <p class="text-sm">Drawn as a panel.</p>
  </Section>
</Story>

<Story
  name="Catalogue cards"
  source="features/catalogue"
  description="The production BuildCard and RecordCard with fixtures."
>
  <Layout as="ul" pattern="grid">
    {#each buildFixtures as build, index (build.id)}
      <BuildCard {build} category={categories[index] ?? ""} as="li" />
    {/each}
  </Layout>
  <Layout as="ul" pattern="grid" min="card-wide">
    {#each recordFixtures as record (record.record_id)}
      <RecordCard {record} as="li" />
    {/each}
  </Layout>
</Story>

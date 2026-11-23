<script lang="ts">
  import Bell from "@lucide/svelte/icons/bell";
  import Button from "@/ui/actions/Button.svelte";
  import Avatar from "@/ui/data/Avatar.svelte";
  import Card from "@/ui/data/Card.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Diff from "@/ui/data/Diff.svelte";
  import Disclosure from "@/ui/data/Disclosure.svelte";
  import Image from "@/ui/data/Image.svelte";
  import List from "@/ui/data/List.svelte";
  import ListItem from "@/ui/data/ListItem.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Timeline from "@/ui/data/Timeline.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const specs = [
    { term: "Dimensions", value: "9 × 7 × 4", mono: true },
    { term: "Opening", value: "8 gt", mono: true },
    { term: "Version", value: "Java 1.21.4" },
    { term: "Creator", value: "CircuitSage", href: "#descriptionlist" },
  ];
</script>

<Story
  name="Card"
  source="data/Card"
  description="With a title and href, the whole card links; inner controls stay clickable."
>
  <Layout as="ul" pattern="grid">
    <Card as="li" title="Linked card" href="#card" eyebrow="Fastest">
      <Text size="sm" tone="muted">The title link covers the card.</Text>
      <div><Button size="sm">Inner action</Button></div>
    </Card>
    <Card as="li" title="With media" href="#card">
      {#snippet media()}<Image
          src="/fixtures/iris-door.svg"
          alt="Iris door illustration"
          width={640}
          height={400}
          fallbackLabel="No preview"
        />{/snippet}
      <Text size="sm" tone="muted">Space Builder</Text>
    </Card>
    <Card as="li" title="Plain surface" tone="muted" padding="lg">
      <Text size="sm">No link.</Text>
      {#snippet footer()}<Text size="xs" tone="muted">Footer</Text>{/snippet}
    </Card>
    <Card as="li" title="Selected" tone="selected" padding="sm" />
    <Card as="li" title="Needs attention" tone="warning" padding="sm" />
  </Layout>
</Story>

<Story name="Image" source="data/Image" description="A fixed frame with a fallback.">
  <Layout pattern="grid" min="tile">
    <Variant label="wide">
      <Image
        src="/fixtures/copper-door.svg"
        alt="Copper door"
        width={640}
        height={400}
        fallbackLabel="No preview"
      />
    </Variant>
    <Variant label="square, contain">
      <Image
        src="/fixtures/iris-door.svg"
        alt="Iris door"
        width={640}
        height={400}
        aspect="square"
        fit="contain"
        fallbackLabel="No preview"
      />
    </Variant>
    <Variant label="missing">
      <Image src={null} alt="" width={640} height={400} fallbackLabel="No preview available" />
    </Variant>
    <Variant label="failed to load">
      <Image
        src="https://media.fixture.invalid/broken.png"
        alt="Missing preview"
        width={640}
        height={400}
        fallbackLabel="Preview could not be loaded"
      />
    </Variant>
  </Layout>
</Story>

<Story name="Avatar" source="data/Avatar" description="Initials when there is no picture.">
  <div class="flex items-center gap-3">
    <Avatar name="CircuitSage" size="sm" />
    <Avatar name="Space Builder" />
    <Avatar name="红石猫" size="lg" alt="红石猫" />
    <Avatar name="Broken Link" src="https://media.fixture.invalid/broken.png" />
  </div>
</Story>

<Story name="DescriptionList" source="data/DescriptionList">
  {#each ["stacked", "inline", "grid"] as const as layout (layout)}
    <Variant label={layout}>
      <DescriptionList {layout} items={specs} divided={layout === "inline"} />
    </Variant>
  {/each}
</Story>

<Story name="StatGroup" source="data/StatGroup">
  <StatGroup
    items={[
      { label: "Builds", value: "1,284" },
      { label: "Records held", value: "12", href: "#statgroup" },
      { label: "Opening", value: "8", unit: "gt", hint: "3 gt faster than the last holder" },
    ]}
  />
</Story>

<Story
  name="Disclosure"
  source="data/Disclosure"
  description="Native details; works without JavaScript."
>
  <Disclosure summary="Door evidence">
    <Text size="sm">Measured with a tick counter on Java 1.21.4.</Text>
  </Disclosure>
  <Disclosure summary="Expansion parameters" variant="card" open>
    <DescriptionList layout="inline" items={specs.slice(0, 2)} />
  </Disclosure>
</Story>

<Story name="List" source="data/List">
  <List label="Notifications">
    <ListItem
      title="Your door Copper Bolt now holds 2 records"
      href="#list"
      unread
      unreadLabel="Unread"
    >
      {#snippet leading()}<Bell class="mt-0.5 size-5 text-fg-muted" aria-hidden="true" />{/snippet}
      {#snippet meta()}<time datetime="2026-01-12">3 days ago</time><Badge tone="success"
          >Record</Badge
        >{/snippet}
    </ListItem>
    <ListItem title="Laptop CLI" description="Last used yesterday">
      {#snippet leading()}<Avatar name="Laptop CLI" size="sm" />{/snippet}
      {#snippet trailing()}<Button size="sm" variant="danger">Revoke</Button>{/snippet}
    </ListItem>
    <ListItem title="Compact row" density="compact" />
  </List>
</Story>

<Story name="ListItem" source="data/ListItem" description="Rows of a List; see the List story.">
  <Text size="sm" tone="muted">Shown above: unread, leading, meta, trailing, and compact.</Text>
</Story>

<Story name="Timeline" source="data/Timeline">
  <Timeline
    label="Record holders"
    items={[
      {
        id: 1,
        datetime: "2026-01-12",
        dateLabel: "12 Jan 2026",
        title: "Copper Bolt 5×5",
        href: "#timeline",
        tone: "accent",
        description: "8 gt, 3 gt faster",
      },
      {
        id: 2,
        datetime: "2025-08-02",
        dateLabel: "2 Aug 2025",
        title: "Iris door",
        tone: "default",
      },
      {
        id: 3,
        datetime: "2024-03-19",
        dateLabel: "19 Mar 2024",
        title: "First holder",
        tone: "success",
      },
    ]}
  />
</Story>

<Story name="Diff" source="data/Diff">
  <Diff
    caption="Changes to Copper Bolt 5×5"
    labels={{
      field: "Field",
      before: "Before",
      after: "After",
      empty: "Empty",
      added: "Added",
      removed: "Removed",
      changed: "Changed",
      unchanged: "Unchanged",
    }}
    showUnchanged
    rows={[
      { label: "Opening time", before: "9 gt", after: "8 gt", kind: "changed" },
      { label: "Video", before: null, after: "https://youtu.be/…", kind: "added" },
      { label: "Notes", before: "Old notes", after: null, kind: "removed" },
      { label: "Width", before: "5", after: "5", kind: "unchanged" },
    ]}
  />
</Story>

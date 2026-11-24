<script lang="ts">
  import * as m from "@/locales/messages-data.gen";
  import Bell from "@lucide/svelte/icons/bell";
  import Button from "@/ui/actions/Button.svelte";
  import Avatar from "@/ui/data/Avatar.svelte";
  import Card from "@/ui/data/Card.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Diff from "@/ui/data/Diff.svelte";
  import Disclosure from "@/ui/data/Disclosure.svelte";
  import Gallery from "@/ui/data/Gallery.svelte";
  import Image from "@/ui/data/Image.svelte";
  import List from "@/ui/data/List.svelte";
  import ListItem from "@/ui/data/ListItem.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Timeline from "@/ui/data/Timeline.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const specs = [
    { term: m.buildDimensions(), value: "9 × 7 × 4", numeric: true },
    { term: m.buildOpening(), value: m.story8Gt(), numeric: true },
    { term: m.storyVersion(), value: m.storyJava1214() },
    { term: m.storyCreator(), value: "CircuitSage", href: "#descriptionlist" },
  ];
</script>

<Story name="Card" source="data/Card" description={m.storyCardDescription()}>
  <Layout as="ul" pattern="grid">
    <Card as="li" title={m.storyLinkedCard()} href="#card" eyebrow={m.storyFastest()}>
      <Text size="sm" tone="muted">{m.storyTheTitleLinkCoversTheCard()}</Text>
      <div><Button size="sm">{m.storyInnerAction()}</Button></div>
    </Card>
    <Card as="li" title={m.storyWithMedia()} href="#card">
      {#snippet media()}<Image
          src="/fixtures/iris-door.svg"
          alt={m.storyIrisDoorIllustration()}
          width={640}
          height={400}
          fallbackLabel={m.storyNoPreview()}
        />{/snippet}
      <Text size="sm" tone="muted">Space Builder</Text>
    </Card>
    <Card as="li" title={m.storyPlainSurface()} tone="muted" padding="lg">
      <Text size="sm">{m.storyNoLink()}</Text>
      {#snippet footer()}<Text size="xs" tone="muted">{m.storyFooter()}</Text>{/snippet}
    </Card>
    <Card as="li" title={m.storySelected()} tone="selected" padding="sm" />
    <Card as="li" title={m.storyNeedsAttention()} tone="warning" padding="sm" />
  </Layout>
</Story>

<Story name="Image" source="data/Image" description={m.storyImageDescription()}>
  <Layout pattern="grid" min="tile">
    <Variant label={m.storyWide()}>
      <Image
        src="/fixtures/copper-door.svg"
        alt={m.storyCopperDoor()}
        width={640}
        height={400}
        fallbackLabel={m.storyNoPreview()}
      />
    </Variant>
    <Variant label={m.storySquareContain()}>
      <Image
        src="/fixtures/iris-door.svg"
        alt={m.storyIrisDoor()}
        width={640}
        height={400}
        aspect="square"
        fit="contain"
        fallbackLabel={m.storyNoPreview()}
      />
    </Variant>
    <Variant label={m.storyMissing()}>
      <Image src={null} alt="" width={640} height={400} fallbackLabel={m.buildNoPreview()} />
    </Variant>
    <Variant label={m.storyFailedToLoad()}>
      <Image
        src="https://media.fixture.invalid/broken.png"
        alt={m.storyMissingPreview()}
        width={640}
        height={400}
        fallbackLabel={m.buildImageUnavailable()}
      />
    </Variant>
  </Layout>
</Story>

<Story name="Gallery" source="data/Gallery" description={m.storyGalleryDescription()}>
  <Gallery
    class="max-w-form"
    items={[
      { file: "copper-door", alt: m.storyCopperDoorFront(), caption: m.storyClosedFromTheFront() },
      { file: "iris-door", alt: m.storyIrisDoorOpen() },
      {
        file: "copper-door",
        alt: m.storyCopperDoorWiring(),
        caption: m.storyTheWiringBehindTheDoor(),
      },
    ].map(({ file, alt, caption }, index) => ({
      kind: "image" as const,
      src: `/fixtures/${file}.svg?view=${index}`,
      thumb: `/fixtures/${file}.svg?view=${index}`,
      width: 640,
      height: 400,
      alt,
      caption,
    }))}
    labels={{
      previous: m.commonPrevious(),
      next: m.commonNext(),
      close: m.storyClose(),
      openItem: m.storyViewFullSize(),
      fallback: m.storyImageCouldNotBeLoaded(),
      position: (current, total) => m.storyImagePosition({ current, total }),
    }}
  />
</Story>

<Story name="Avatar" source="data/Avatar" description={m.storyAvatarDescription()}>
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
      { label: m.navBuilds(), value: "1,284" },
      { label: m.storyRecordsHeld(), value: "12", href: "#statgroup" },
      {
        label: m.buildOpening(),
        value: "8",
        unit: "gt",
        hint: m.story3GtFasterThanTheLastHolder(),
      },
    ]}
  />
</Story>

<Story name="Disclosure" source="data/Disclosure" description={m.storyDisclosureDescription()}>
  <Disclosure summary={m.storyDoorEvidence()}>
    <Text size="sm">{m.storyMeasuredWithATickCounterOnJava1()}</Text>
  </Disclosure>
  <Disclosure summary={m.storyExpansionParameters()} variant="card" open>
    <DescriptionList layout="inline" items={specs.slice(0, 2)} />
  </Disclosure>
</Story>

<Story name="List" source="data/List">
  <List label={m.storyNotifications()}>
    <ListItem
      title={m.storyYourDoorCopperBoltNowHolds2Records()}
      href="#list"
      unread
      unreadLabel={m.storyUnread()}
    >
      {#snippet leading()}<Bell class="mt-0.5 size-5 text-fg-muted" aria-hidden="true" />{/snippet}
      {#snippet meta()}<time datetime="2026-01-12">{m.story3DaysAgo()}</time>{/snippet}
    </ListItem>
    <ListItem title={m.storyLaptopCli()} description={m.storyLastUsedYesterday()}>
      {#snippet leading()}<Avatar name="Laptop CLI" size="sm" />{/snippet}
      {#snippet trailing()}<Button size="sm" variant="danger">{m.storyRevoke()}</Button>{/snippet}
    </ListItem>
    <ListItem title={m.storyCompactRow()} density="compact" />
  </List>
</Story>

<Story name="ListItem" source="data/ListItem" description={m.storyListItemDescription()}>
  <Text size="sm" tone="muted">{m.storyShownAboveUnreadLeadingMetaTrailingAndCompact()}</Text>
</Story>

<Story name="Timeline" source="data/Timeline">
  <Timeline
    label={m.storyRecordHolders()}
    items={[
      {
        id: 1,
        datetime: "2026-01-12",
        dateLabel: m.story12Jan2026(),
        title: m.storyCopperBolt55(),
        href: "#timeline",
        tone: "accent",
        description: m.story8Gt3GtFaster(),
      },
      {
        id: 2,
        datetime: "2025-08-02",
        dateLabel: m.story2Aug2025(),
        title: m.storyIrisDoor(),
        tone: "default",
      },
      {
        id: 3,
        datetime: "2024-03-19",
        dateLabel: m.story19Mar2024(),
        title: m.storyFirstHolder(),
        tone: "success",
      },
    ]}
  />
</Story>

<Story name="Diff" source="data/Diff">
  <Diff
    caption={m.storyChangesToCopperBolt55()}
    labels={{
      field: m.storyField(),
      before: m.storyBefore(),
      after: m.storyAfter(),
      empty: m.storyEmpty(),
      added: m.storyAdded(),
      removed: m.storyRemoved(),
      changed: m.storyChanged(),
      unchanged: m.storyUnchanged(),
    }}
    showUnchanged
    rows={[
      { label: m.storyOpeningTime(), before: m.story9Gt(), after: m.story8Gt(), kind: "changed" },
      { label: m.storyVideo(), before: null, after: "https://youtu.be/…", kind: "added" },
      { label: m.storyNotes(), before: m.storyOldNotes(), after: null, kind: "removed" },
      { label: m.storyWidth(), before: "5", after: "5", kind: "unchanged" },
    ]}
  />
</Story>

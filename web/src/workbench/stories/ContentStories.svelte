<script lang="ts">
  import Hammer from "@lucide/svelte/icons/hammer";
  import Code from "@/ui/content/Code.svelte";
  import Heading from "@/ui/content/Heading.svelte";
  import Icon from "@/ui/content/Icon.svelte";
  import Link from "@/ui/content/Link.svelte";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Prose from "@/ui/content/Prose.svelte";
  import SkipLink from "@/ui/content/SkipLink.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Timestamp from "@/ui/content/Timestamp.svelte";
  import VisuallyHidden from "@/ui/content/VisuallyHidden.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const traceback = [
    "Traceback (most recent call last):",
    '  File "squid/core/records.py", line 42, in resolve',
    "ValueError: no candidate",
  ].join("\n");
</script>

<Story
  name="Heading"
  source="content/Heading"
  description="The level sets the outline; the size sets the look."
>
  {#each ["display", "xl", "lg", "md", "sm"] as const as size (size)}
    <Variant label={`level 3, size ${size}`}>
      <Heading level={3} {size}>Fastest 3×3 flush door</Heading>
    </Variant>
  {/each}
  <Variant label="eyebrow">
    <Heading level={3} eyebrow="Fastest">Copper Bolt 5×5</Heading>
  </Variant>
</Story>

<Story
  name="MetaList"
  source="content/MetaList"
  description="Facts about one item, separated by space rather than punctuation."
>
  <Variant label="sm">
    <MetaList items={["Door", "All time", "2 holders", "Computed Jan 12, 2026"]} />
  </Variant>
  <Variant label="xs, wrapping">
    <MetaList
      size="xs"
      items={["Java 1.21", "Bedrock 1.20", "12 × 12 × 5", "8 gt opening", "Updated 3 days ago"]}
    />
  </Variant>
</Story>

<Story name="Text" source="content/Text">
  <Variant label="tones">
    {#each ["default", "muted", "success", "warning", "danger"] as const as tone (tone)}
      <Text {tone}>A {tone} line of body text.</Text>
    {/each}
  </Variant>
  <Variant label="sizes, numeric, semibold">
    <Text size="xs">Extra small hint text</Text>
    <Text size="sm" weight="semibold">Small semibold</Text>
    <Text numeric>9 × 7 × 4, 8 gt</Text>
  </Variant>
  <Variant label="clamped to one line">
    <Text lines={1} class="max-w-64"
      >A very long creator list that does not fit: CircuitSage, Space Builder, 红石猫</Text
    >
  </Variant>
</Story>

<Story name="Link" source="content/Link">
  <Variant label="inline">
    <Text>Read the <Link href="#link">door rules</Link> before submitting.</Text>
  </Variant>
  <Variant label="standalone, subtle, current, external">
    <Link href="#link" variant="standalone">View all records</Link>
    <Link href="#link" variant="subtle">Edited 3 days ago</Link>
    <Link href="#link" variant="standalone" current>Current page</Link>
    <Link href="https://example.com" externalLabel="opens another site">Discord</Link>
  </Variant>
</Story>

<Story name="Icon" source="content/Icon" description="Decorative unless given a label.">
  <div class="flex items-center gap-4">
    <Icon icon={Hammer} />
    <Icon icon={Hammer} size="md" />
    <Icon icon={Hammer} size="lg" label="Builder" />
  </div>
</Story>

<Story name="Code" source="content/Code">
  <Variant label="inline and key">
    <Text>Search with <Code>width:5..8</Code>, then press <Code variant="key">Enter</Code>.</Text>
  </Variant>
  <Variant label="block">
    <Code variant="block" label="Traceback">{traceback}</Code>
  </Variant>
</Story>

<Story name="Timestamp" source="content/Timestamp">
  <Text
    >Approved <Timestamp
      datetime="2026-01-12T00:00:00Z"
      label="3 days ago"
      title="12 January 2026"
    /></Text
  >
</Story>

<Story name="Prose" source="content/Prose" description="Styles rendered Markdown.">
  <Prose>
    <h2>Door rules</h2>
    <p>A <strong>flush</strong> door leaves no blocks protruding when closed.</p>
    <ul>
      <li>Opening time is measured in game ticks.</li>
      <li>Closing time starts when the input turns off.</li>
    </ul>
    <blockquote>Record checks compare the true opening time.</blockquote>
  </Prose>
</Story>

<Story
  name="VisuallyHidden"
  source="content/VisuallyHidden"
  description="Text for screen readers only."
>
  <Text>3 records <VisuallyHidden>held by this build</VisuallyHidden></Text>
</Story>

<Story
  name="SkipLink"
  source="content/SkipLink"
  description="Hidden until focused; the page layouts render one first."
>
  <SkipLink href="#heading" label="Skip to the heading story" />
  <Text tone="muted">Press Tab from the top of the page to reveal it.</Text>
</Story>

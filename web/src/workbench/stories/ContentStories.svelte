<script lang="ts">
  import * as m from "@/locales/messages-content.gen";
  import Hammer from "@lucide/svelte/icons/hammer";
  import Code from "@/ui/content/Code.svelte";
  import CodeBlock from "@/ui/content/CodeBlock.svelte";
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
  const python = [
    "def door_area(width: int, height: int) -> int:",
    '    """Blocks in the opening of a door."""',
    "    return width * height  # a 3x3 door is 9",
  ].join("\n");
  const toneExamples = {
    default: m.storySeamless33FlushDoor,
    muted: m.storySubmittedByCircuitsage,
    success: m.storyRecordConfirmed,
    warning: m.storyThisBuildIsPendingReview,
    danger: m.storyEvidenceVideoMissing,
  };
</script>

<Story name="Heading" source="content/Heading" description={m.storyHeadingDescription()}>
  {#each ["display", "xl", "lg", "md", "sm"] as const as size (size)}
    <Variant label={m.storyHeadingSize({ size })}>
      <Heading level={3} {size}>{m.storyFastest33FlushDoor()}</Heading>
    </Variant>
  {/each}
  <Variant label={m.storyEyebrow()}>
    <Heading level={3} eyebrow={m.storyFastest()}>{m.storyCopperBolt55()}</Heading>
  </Variant>
</Story>

<Story name="MetaList" source="content/MetaList" description={m.storyMetaListDescription()}>
  <Variant label={m.storySm()}>
    <MetaList items={[m.story2Holders(), m.storyCurrentVersion()]} />
  </Variant>
  <Variant label={m.storyXs()}>
    <MetaList size="xs" items={[m.storyJava121(), m.storyBedrockVersion()]} />
  </Variant>
</Story>

<Story name="Text" source="content/Text">
  <Variant label={m.storyTones()}>
    {#each ["default", "muted", "success", "warning", "danger"] as const as tone (tone)}
      <Text {tone}>{toneExamples[tone]()}</Text>
    {/each}
  </Variant>
  <Variant label={m.storySizesNumericSemibold()}>
    <Text size="xs">{m.storyMeasuredWithATickCounterOnJava1()}</Text>
    <Text size="sm" weight="semibold">{m.storyCopperBolt55()}</Text>
    <Text numeric>{m.story9748Gt()}</Text>
  </Variant>
  <Variant label={m.storyClampedToOneLine()}>
    <Text lines={1} class="max-w-64">{m.storyByCircuitsageAndOthers()}</Text>
  </Variant>
</Story>

<Story name="Link" source="content/Link">
  <Variant label={m.storyInline()}>
    <Text
      >{m.storyReadThe()}
      <Link href="#link">{m.storyDoorRules()}</Link>
      {m.storyBeforeSubmitting()}</Text
    >
  </Variant>
  <Variant label={m.storyStandaloneSubtleCurrentExternal()}>
    <Link href="#link" variant="standalone">{m.storyViewAllRecords()}</Link>
    <Link href="#link" variant="subtle">{m.storyEdited3DaysAgo()}</Link>
    <Link href="#link" variant="standalone" current>{m.navRecords()}</Link>
    <Link href="https://example.com" externalLabel={m.storyOpensAnotherSite()}>Discord</Link>
  </Variant>
</Story>

<Story name="Icon" source="content/Icon" description={m.storyIconDescription()}>
  <div class="flex items-center gap-4">
    <Icon icon={Hammer} />
    <Icon icon={Hammer} size="md" />
    <Icon icon={Hammer} size="lg" label={m.storyBuilder()} />
  </div>
</Story>

<Story name="Code" source="content/Code">
  <Variant label={m.storyInlineAndKey()}>
    <Text
      >{m.storySearchWith()}
      <Code>width:5..8</Code>{m.storyThenPress()}
      <Code variant="key">{m.storyEnter()}</Code>.</Text
    >
  </Variant>
</Story>

<Story name="CodeBlock" source="content/CodeBlock">
  <Variant label={m.storyBlock()}>
    <CodeBlock code={traceback} label={m.storyTraceback()} />
  </Variant>
  <Variant label={m.storyHighlightedPython()}>
    <CodeBlock code={python} lang="python" label={m.storyHighlightedPython()} />
  </Variant>
</Story>

<Story name="Timestamp" source="content/Timestamp">
  <Text
    >{m.storyApproved()}
    <Timestamp
      datetime="2026-01-12T00:00:00Z"
      label={m.story3DaysAgo()}
      title={m.story12January2026()}
    /></Text
  >
</Story>

<Story name="Prose" source="content/Prose" description={m.storyProseDescription()}>
  <Prose>
    <h2>{m.storyDoorRulesLabel()}</h2>
    <p>
      {m.storyA()} <strong>{m.storyFlush()}</strong>
      {m.storyDoorLeavesNoBlocksProtrudingWhenClosed()}
    </p>
    <ul>
      <li>{m.storyOpeningTimeIsMeasuredInGameTicks()}</li>
      <li>{m.storyClosingTimeStartsWhenTheInputTurnsOff()}</li>
    </ul>
    <blockquote>{m.storyRecordChecksCompareTheTrueOpeningTime()}</blockquote>
  </Prose>
</Story>

<Story
  name="VisuallyHidden"
  source="content/VisuallyHidden"
  description={m.storyVisuallyHiddenDescription()}
>
  <Text>{m.story3Records()} <VisuallyHidden>{m.storyHeldByThisBuild()}</VisuallyHidden></Text>
</Story>

<Story name="SkipLink" source="content/SkipLink" description={m.storySkipLinkDescription()}>
  <Variant label={m.storyHiddenUntilFocused()}>
    <SkipLink href="#heading" label={m.storySkipToTheHeadingStory()} />
  </Variant>
</Story>

<script lang="ts">
  import Hammer from "@lucide/svelte/icons/hammer";
  import Tag from "@lucide/svelte/icons/tag";
  import Button from "@/ui/actions/Button.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Chip from "@/ui/feedback/Chip.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import ErrorState from "@/ui/feedback/ErrorState.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Progress from "@/ui/feedback/Progress.svelte";
  import Skeleton from "@/ui/feedback/Skeleton.svelte";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const tones = ["neutral", "info", "success", "warning", "danger", "accent"] as const;
  let flush = $state(true);
  let chips = $state(["width ≥ 5", "flush"]);
</script>

<Story
  name="Badge"
  source="feedback/Badge"
  description="Static metadata; the text carries the meaning."
>
  <Variant label="tones">
    <div class="flex flex-wrap gap-2">
      {#each tones as tone (tone)}<Badge {tone}>{tone}</Badge>{/each}
    </div>
  </Variant>
  <Variant label="dot, icon, size md">
    <div class="flex flex-wrap gap-2">
      <Badge tone="success" dot>Approved</Badge>
      <Badge tone="warning" dot>Pending</Badge>
      <Badge icon={Hammer}>Door</Badge>
      <Badge size="md" tone="accent">Record holder</Badge>
    </div>
  </Variant>
</Story>

<Story name="Chip" source="feedback/Chip" description="Tags people can follow, toggle, or remove.">
  <Variant label="link, toggle, static">
    <div class="flex flex-wrap gap-2">
      <Chip href="#chip" icon={Tag}>Seamless</Chip>
      <Chip pressed={flush} onclick={() => (flush = !flush)}>Flush</Chip>
      <Chip>3×3</Chip>
      <Chip tone="accent">Selected</Chip>
    </div>
  </Variant>
  <Variant label="removable (link and button)">
    <div class="flex flex-wrap gap-2">
      <Chip removeLabel="Remove version filter" removeHref="#chip">Java 1.21</Chip>
      {#each chips as chip (chip)}
        <Chip
          removeLabel={`Remove ${chip}`}
          onRemove={() => (chips = chips.filter((item) => item !== chip))}>{chip}</Chip
        >
      {/each}
    </div>
  </Variant>
</Story>

<Story
  name="Notice"
  source="feedback/Notice"
  description="A message about a page, region, or operation."
>
  <Notice tone="info" title="No matching builds">Try a broader size or another version.</Notice>
  <Notice tone="success" title="Draft saved" role="status">Your changes are stored.</Notice>
  <Notice tone="warning" title="Couldn’t load builds">
    Your filters are preserved. Try again shortly.
    {#snippet actions()}<Button size="sm">Retry</Button>{/snippet}
  </Notice>
  <Notice tone="danger" title="Upload failed" role="alert"
    >Retry with the same file or choose another.</Notice
  >
  <Notice tone="warning" variant="banner">This build is pending review.</Notice>
</Story>

<Story name="Progress" source="feedback/Progress">
  <Progress label="Uploading door.schem" value={40} valueLabel="40%" />
  <Progress label="Rendering preview" />
  <Progress
    label="Votes"
    kind="meter"
    value={3}
    max={5}
    valueLabel="3 of 5 votes"
    tone="success"
    thresholds={[{ value: 4, label: "Approve at 4" }]}
  />
</Story>

<Story name="Spinner" source="feedback/Spinner" description="Inline beside one line of text.">
  <p class="flex items-center gap-2 text-sm"><Spinner /> Checking the record…</p>
  <Spinner size="md" label="Loading" />
</Story>

<Story
  name="Skeleton"
  source="feedback/Skeleton"
  description="Shape of content loading in an island."
>
  <div class="grid max-w-96 gap-4" role="group" aria-busy="true" aria-label="Loading builds">
    <Skeleton shape="heading" width="1/2" />
    <Skeleton lines={3} />
    <Skeleton shape="block" />
    <div class="flex items-center gap-3"><Skeleton shape="avatar" /><Skeleton width="1/3" /></div>
    <Skeleton shape="row" />
  </div>
</Story>

<Story name="EmptyState" source="feedback/EmptyState">
  <EmptyState title="No drafts yet" description="Start a submission to see it here.">
    {#snippet actions()}<Button variant="primary">Submit a build</Button>{/snippet}
  </EmptyState>
  <EmptyState title="No records match" size="page" />
</Story>

<Story name="ErrorState" source="feedback/ErrorState">
  <ErrorState
    title="Couldn’t load schematics"
    description="The catalogue API did not answer in time."
    retryLabel="Try again"
    retryHref="#errorstate"
    reference={{ label: "Reference", value: "ERR-7F3A" }}
  />
</Story>

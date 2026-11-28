<script lang="ts">
  import { formatList } from "@/locales/format";
  import { cn } from "@/ui/cn";
  import Link from "@/ui/content/Link.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import BuildPreview from "./BuildPreview.svelte";
  import { buildHref, buildName, type BuildIdentity } from "./presentation";

  /**
   * A build named by its thumbnail and linked title, wherever a build is referred to rather
   * than shown: a record's holder, a history period, a prediction. `inline` sits in a line of
   * text; `block` adds the creators underneath, for a holder under a record card.
   */
  type Props = { build: BuildIdentity; layout?: "inline" | "block" };
  let { build, layout = "inline" }: Props = $props();
  const creators = $derived(formatList((build.creators ?? []).map((creator) => creator.name)));
</script>

<Layout pattern="cluster" gap="xs" align="center" wrap={false}>
  <BuildPreview
    {build}
    thumbnail="square"
    class={cn("shrink-0 rounded-xs", layout === "inline" ? "size-6" : "size-10")}
  />
  <!-- A flex column, not a grid: a grid of truncated text beside the square thumbnail sends
       Chromium's intrinsic sizing into minutes of layout at narrow widths. -->
  <span class="flex min-w-0 flex-col">
    <Link href={buildHref(build.id)} variant="standalone">{buildName(build)}</Link>
    {#if layout === "block" && creators !== ""}
      <Text as="span" size="xs" tone="muted" lines={1}>{creators}</Text>
    {/if}
  </span>
</Layout>

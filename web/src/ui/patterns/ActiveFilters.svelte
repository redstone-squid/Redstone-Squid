<script lang="ts">
  import Link from "@/ui/content/Link.svelte";
  import { cn } from "@/ui/cn";
  import Chip from "@/ui/feedback/Chip.svelte";

  /**
   * The filters applied to a listing, each a chip whose remove link is the same URL without
   * it, plus a link that clears them all. Renders nothing when no filter is active.
   * `removeLabel` names each remove link, such as "Remove width ≥ 5".
   */
  type Props = {
    items: { label: string; removeLabel: string; removeHref: string }[];
    clearHref: string;
    labels: { region: string; clear: string };
    class?: string;
  };
  let { items, clearHref, labels, class: className }: Props = $props();
</script>

{#if items.length > 0}
  <div
    role="group"
    aria-label={labels.region}
    class={cn("flex min-w-0 flex-wrap items-center gap-2", className)}
  >
    <ul class="flex min-w-0 flex-wrap gap-2">
      {#each items as item (item.removeHref)}
        <li class="min-w-0">
          <Chip tone="accent" removeLabel={item.removeLabel} removeHref={item.removeHref}
            >{item.label}</Chip
          >
        </li>
      {/each}
    </ul>
    <Link href={clearHref} variant="standalone" class="text-sm">{labels.clear}</Link>
  </div>
{/if}

<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import Skeleton from "@/ui/feedback/Skeleton.svelte";
  import Layout from "@/ui/layout/Layout.svelte";

  /**
   * A listing's results as `cards` or a `table`, by `view`, with its other states. Server
   * pages pass `ready`, `empty` or `error`. Islands pass `loading` before the first results,
   * shown as skeletons after 200 ms so quick responses don't flash, and `refreshing` while
   * newer results load, which keeps the old ones visible and dimmed. Both mark the region
   * busy under `busyLabel`. `id` is the target of pagination links (`#results`).
   */
  type Props = {
    view: "cards" | "table";
    status?: "ready" | "refreshing" | "loading" | "empty" | "error";
    /** Names the region while it is busy, such as "Loading builds". */
    busyLabel?: string;
    cards?: Snippet;
    table?: Snippet;
    empty?: Snippet;
    error?: Snippet;
    id?: string;
    class?: string;
  };
  let {
    view,
    status = "ready",
    busyLabel,
    cards,
    table,
    empty,
    error,
    id = "results",
    class: className,
  }: Props = $props();
  const busy = $derived(status === "loading" || status === "refreshing");

  let skeletons = $state(false);
  $effect(() => {
    skeletons = false;
    if (status !== "loading") return;
    const timer = setTimeout(() => (skeletons = true), 200);
    return () => clearTimeout(timer);
  });
</script>

<div
  {id}
  class={cn("min-w-0", className)}
  role={busy ? "region" : undefined}
  aria-label={busy ? busyLabel : undefined}
  aria-busy={busy ? "true" : undefined}
>
  {#if status === "loading"}
    {#if skeletons}
      {#if view === "table"}
        <div class="grid gap-2">
          {#each { length: 6 }, index (index)}<Skeleton shape="row" />{/each}
        </div>
      {:else}
        <Layout pattern="grid">
          {#each { length: 6 }, index (index)}
            <div class="grid gap-3">
              <Skeleton shape="block" /><Skeleton shape="heading" width="3/4" /><Skeleton />
            </div>
          {/each}
        </Layout>
      {/if}
    {/if}
  {:else if status === "empty"}
    {@render empty?.()}
  {:else if status === "error"}
    {@render error?.()}
  {:else}
    <div
      class={cn(
        "min-w-0 motion-safe:transition-opacity motion-safe:duration-fast",
        status === "refreshing" && "pointer-events-none opacity-60",
      )}
    >
      {@render (view === "table" ? table : cards)?.()}
    </div>
  {/if}
</div>

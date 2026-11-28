<script lang="ts">
  import { cn } from "@/ui/cn";

  type Stat = { label: string; value: string; unit?: string; hint?: string; href?: string };

  /**
   * Headline numbers, such as a build's size and speed or a creator's record count. `tiles`
   * draws each in a bordered tile; `inline` sets them in a wrapping row inside a card or
   * header. `lead` keeps only the first at full size and mutes the rest, for numbers ranked
   * by importance, such as the measurements that decide a record.
   */
  type Props = {
    items: Stat[];
    size?: "sm" | "md" | "lg";
    layout?: "tiles" | "inline";
    lead?: boolean;
    class?: string;
  };
  let { items, size = "md", layout = "tiles", lead = false, class: className }: Props = $props();
  const sizes = { sm: "text-lg", md: "text-2xl", lg: "text-3xl" };
  const followers = { sm: "text-base", md: "text-lg", lg: "text-xl" };
</script>

<dl
  class={cn(
    "min-w-0",
    layout === "tiles"
      ? "grid grid-cols-fill-tile gap-4"
      : "flex flex-wrap items-end gap-x-6 gap-y-2",
    className,
  )}
>
  {#each items as stat, index (stat.label)}
    <div
      class={cn(
        "grid min-w-0 content-start",
        layout === "tiles" ? "gap-1 rounded-sm border border-line bg-surface p-4" : "gap-0.5",
      )}
    >
      <dt class={cn("text-fg-muted", layout === "tiles" ? "text-sm" : "text-xs")}>{stat.label}</dt>
      <dd
        class={cn(
          "font-bold tracking-tight wrap-break-word tabular-nums",
          lead && index > 0 ? [followers[size], "text-fg-muted"] : sizes[size],
        )}
      >
        {#if stat.href !== undefined}<a class="no-underline hover:underline" href={stat.href}
            >{stat.value}</a
          >{:else}{stat.value}{/if}{#if stat.unit}<span
            class="ms-1 text-sm font-normal tracking-normal text-fg-muted">{stat.unit}</span
          >{/if}
      </dd>
      {#if stat.hint}<dd class="text-xs text-fg-muted">{stat.hint}</dd>{/if}
    </div>
  {/each}
</dl>

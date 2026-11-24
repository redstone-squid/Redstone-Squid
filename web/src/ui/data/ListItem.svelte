<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * One row of a `List`: an optional `leading` icon, avatar, or thumbnail; a title, linked to
   * `href` across the whole row; `meta` such as badges and times; and `trailing` actions,
   * which stay clickable above the row link. `unread` adds a marker and announces
   * `unreadLabel` before the title.
   */
  type Props = {
    title: string;
    href?: string;
    description?: string;
    unread?: boolean;
    unreadLabel?: string;
    density?: "comfortable" | "compact";
    leading?: Snippet;
    meta?: Snippet;
    trailing?: Snippet;
    children?: Snippet;
  };
  let {
    title,
    href,
    description,
    unread = false,
    unreadLabel,
    density = "comfortable",
    leading,
    meta,
    trailing,
    children,
  }: Props = $props();
</script>

<li
  class={cn(
    "relative isolate flex min-w-0 items-start gap-3",
    density === "compact" ? "px-3 py-2" : "px-4 py-3",
    href !== undefined &&
      "hover:bg-surface-muted has-[[data-row-link]:focus-visible]:outline-3 has-[[data-row-link]:focus-visible]:-outline-offset-3 has-[[data-row-link]:focus-visible]:outline-focus",
    unread && "bg-accent-soft/40",
  )}
>
  {#if unread}<span
      class="mt-2 size-2 shrink-0 rounded-full forced-highlight bg-accent"
      aria-hidden="true"
    ></span>{/if}
  {#if leading}<div class="shrink-0">{@render leading()}</div>{/if}
  <div class="grid min-w-0 flex-1 gap-1">
    <p class={cn("min-w-0 wrap-break-word", unread ? "font-semibold" : "font-medium")}>
      {#if unread && unreadLabel !== undefined}<span class="sr-only">{unreadLabel}:</span>{/if}
      {#if href !== undefined}<a
          {href}
          data-row-link
          class="no-underline after:absolute after:inset-0 after:content-[''] hover:underline focus-visible:outline-none"
          >{title}</a
        >{:else}{title}{/if}
    </p>
    {#if description}<p class="text-sm wrap-break-word text-fg-muted">{description}</p>{/if}
    {#if meta}<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
        {@render meta()}
      </div>{/if}
    {@render children?.()}
  </div>
  {#if trailing}<div class="relative z-sticky flex shrink-0 items-center gap-2">
      {@render trailing()}
    </div>{/if}
</li>

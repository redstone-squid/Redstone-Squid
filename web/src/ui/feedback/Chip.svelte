<script lang="ts">
  import X from "@lucide/svelte/icons/x";
  import type { LucideIcon } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * A tag people interact with: a link (`href`), a toggle (`pressed` with `onclick`), or a
   * static token. A removable chip renders a separate remove control named `removeLabel`
   * (such as "Remove width ≥ 5"), as a link when `removeHref` is given so it works without
   * JavaScript, otherwise as a button calling `onRemove`.
   */
  type Props = {
    href?: string;
    pressed?: boolean;
    onclick?: () => void;
    icon?: LucideIcon;
    tone?: "neutral" | "accent";
    removeLabel?: string;
    removeHref?: string;
    onRemove?: () => void;
    class?: string;
    children: Snippet;
  };
  let {
    href,
    pressed,
    onclick,
    icon: Glyph,
    tone = "neutral",
    removeLabel,
    removeHref,
    onRemove,
    class: className,
    children,
  }: Props = $props();
  const removable = $derived(removeLabel !== undefined);
  const shell = $derived(
    cn(
      "inline-flex max-w-full min-w-0 items-center rounded-xs border text-sm",
      tone === "accent" || pressed === true
        ? "border-accent bg-accent-soft text-fg"
        : "border-line-strong bg-surface text-fg",
      className,
    ),
  );
  const body = "inline-flex min-h-control-sm min-w-0 items-center gap-1.5 px-2.5 no-underline";
  const interactive = "cursor-pointer rounded-xs hover:bg-surface-muted";
</script>

{#snippet label()}
  {#if Glyph}<Glyph class="size-4 shrink-0" aria-hidden="true" />{/if}<span class="truncate"
    >{@render children()}</span
  >
{/snippet}

<span class={shell}>
  {#if href !== undefined}
    <a {href} class={cn(body, interactive)}>{@render label()}</a>
  {:else if onclick}
    <button
      type="button"
      class={cn(body, interactive, "aria-pressed:forced-highlight")}
      aria-pressed={pressed}
      {onclick}>{@render label()}</button
    >
  {:else}
    <span class={body}>{@render label()}</span>
  {/if}
  {#if removable}
    {#if removeHref !== undefined}
      <a
        href={removeHref}
        class="grid size-control-sm shrink-0 place-items-center rounded-xs hover:bg-surface-muted"
        aria-label={removeLabel}><X class="size-4" aria-hidden="true" /></a
      >
    {:else}
      <button
        type="button"
        class="grid size-control-sm shrink-0 cursor-pointer place-items-center rounded-xs hover:bg-surface-muted"
        aria-label={removeLabel}
        onclick={onRemove}><X class="size-4" aria-hidden="true" /></button
      >
    {/if}
  {/if}
</span>

<script lang="ts">
  import X from "@lucide/svelte/icons/x";
  import type { LucideIcon } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * A tag people interact with: a link (`href`), a toggle (`pressed` with `onclick`), or a
   * static token. A removable chip renders a separate remove control named `removeLabel`
   * (such as "Remove width ≥ 5"), as a link when `removeHref` is given so it works without
   * JavaScript, otherwise as a button calling `onRemove`. `size="sm"` is for chips inside a
   * field's box, which must fit the box's height; its remove target stays above 24px.
   */
  type Props = {
    href?: string;
    pressed?: boolean;
    onclick?: () => void;
    icon?: LucideIcon;
    tone?: "neutral" | "accent";
    size?: "sm" | "md";
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
    size = "md",
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
  const body = $derived(
    cn(
      "inline-flex min-w-0 items-center gap-1.5 no-underline",
      size === "sm" ? "min-h-7 px-2" : "min-h-control-sm px-2.5",
    ),
  );
  const remover = $derived(
    cn(
      "grid shrink-0 place-items-center rounded-xs hover:bg-surface-muted",
      size === "sm" ? "size-7" : "size-control-sm",
    ),
  );
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
      <a href={removeHref} class={remover} aria-label={removeLabel}
        ><X class="size-4" aria-hidden="true" /></a
      >
    {:else}
      <button
        type="button"
        class={cn(remover, "cursor-pointer")}
        aria-label={removeLabel}
        onclick={onRemove}><X class="size-4" aria-hidden="true" /></button
      >
    {/if}
  {/if}
</span>

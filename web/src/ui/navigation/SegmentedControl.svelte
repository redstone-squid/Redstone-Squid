<script lang="ts">
  import type { LucideIcon } from "@lucide/svelte";
  import { cn } from "@/ui/cn";

  /** `lang` is the label's language when it differs from the page's, as for language names. */
  type LinkSegment = {
    label: string;
    href: string;
    current?: boolean;
    icon?: LucideIcon;
    lang?: string;
  };
  type RadioSegment = { label: string; value: string; icon?: LucideIcon; disabled?: boolean };

  /**
   * A compact choice between a few options.
   *
   * In `link` mode (the default) each segment is a link, for views kept in the URL such as
   * cards or table. `radio` mode is a native radio group named `name`, for a choice inside a
   * form such as a vote or a unit; it submits without JavaScript and arrow keys move the choice.
   * In `submit` mode each segment is a submit button named `name`, pressed when it is `value`,
   * for a preference saved as soon as it is picked, such as the theme.
   */
  type Props = { label: string; size?: "sm" | "md"; class?: string } & (
    | { mode?: "link"; items: LinkSegment[] }
    | {
        mode: "radio";
        items: RadioSegment[];
        name: string;
        value?: string;
        disabled?: boolean;
        onchange?: (value: string) => void;
      }
    | { mode: "submit"; items: RadioSegment[]; name: string; value?: string }
  );
  let {
    label,
    size = "sm",
    class: className,
    value = $bindable(),
    ...props
  }: Props & { value?: string } = $props();

  const segmentClass = $derived(
    cn(
      "flex items-center gap-2 rounded-xs px-3 text-sm font-semibold whitespace-nowrap text-fg-muted no-underline hover:text-fg",
      // The track's 2px padding and 1px border make it exactly a control's height.
      size === "sm" ? "min-h-7.5" : "min-h-9.5",
    ),
  );
  const trackClass = "flex flex-wrap rounded-sm border border-line-strong bg-surface p-0.5";
</script>

{#snippet segment(item: LinkSegment | RadioSegment)}
  {#if item.icon}<item.icon class="size-4" aria-hidden="true" />{/if}{item.label}
{/snippet}

{#if props.mode === "radio"}
  {@const radio = props}
  <fieldset class={cn("w-fit max-w-full min-w-0", className)} disabled={radio.disabled}>
    <legend class="sr-only">{label}</legend>
    <div data-indicator class={trackClass}>
      {#each radio.items as item (item.value)}
        <label
          class={cn(
            segmentClass,
            "cursor-pointer has-checked:forced-highlight has-checked:bg-accent-soft has-checked:text-fg",
            "has-focus-visible:outline-3 has-focus-visible:outline-focus",
            "has-disabled:cursor-not-allowed has-disabled:opacity-60 has-disabled:hover:text-fg-muted",
          )}
        >
          <input
            type="radio"
            class="sr-only"
            name={radio.name}
            value={item.value}
            disabled={item.disabled}
            bind:group={value}
            onchange={() => radio.onchange?.(item.value)}
          />{@render segment(item)}
        </label>
      {/each}
    </div>
  </fieldset>
{:else if props.mode === "submit"}
  {@const submit = props}
  <div role="group" aria-label={label} class={cn("w-fit max-w-full", trackClass, className)}>
    {#each submit.items as item (item.value)}
      <button
        type="submit"
        name={submit.name}
        value={item.value}
        disabled={item.disabled}
        aria-pressed={item.value === value ? "true" : "false"}
        class={cn(
          segmentClass,
          "cursor-pointer aria-pressed:forced-highlight aria-pressed:bg-accent-soft aria-pressed:text-fg",
        )}>{@render segment(item)}</button
      >
    {/each}
  </div>
{:else}
  <nav aria-label={label} class={cn("w-fit max-w-full", className)}>
    <ul class={trackClass}>
      {#each props.items as item (item.href)}
        <li>
          <a
            href={item.href}
            lang={item.lang}
            hreflang={item.lang}
            aria-current={item.current === true ? "true" : undefined}
            class={cn(
              segmentClass,
              "current:forced-highlight current:bg-accent-soft current:text-fg",
            )}>{@render segment(item)}</a
          >
        </li>
      {/each}
    </ul>
  </nav>
{/if}

<style>
  /*
   * In radio mode the highlight slides to the new choice. The track draws it behind the
   * segments, anchored to the checked one; `anchor-scope` keeps each group's anchor its own.
   * Without anchor positioning, the checked segment's own background marks it.
   */
  @supports (anchor-scope: all) {
    [data-indicator] {
      position: relative;
      anchor-scope: --checked-segment;
    }
    /* Positioned, so the segments paint above the highlight. */
    [data-indicator] > label {
      position: relative;
    }
    [data-indicator] > label:has(:checked) {
      anchor-name: --checked-segment;
      background-color: transparent;
    }
    [data-indicator]:has(:checked)::before {
      content: "";
      position: absolute;
      position-anchor: --checked-segment;
      inset: anchor(top) anchor(right) anchor(bottom) anchor(left);
      border-radius: var(--radius-xs);
      background-color: var(--color-accent-soft);
    }
    @media (prefers-reduced-motion: no-preference) {
      [data-indicator]::before {
        transition-property: inset;
        transition-duration: var(--duration-normal);
        transition-timing-function: var(--ease-standard);
      }
    }
  }
</style>

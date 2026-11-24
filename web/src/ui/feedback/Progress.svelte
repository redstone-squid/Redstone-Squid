<script lang="ts">
  import { cn } from "@/ui/cn";

  type Threshold = { value: number; label: string };

  /**
   * How far along something is. `progress` is an operation, such as an upload; omit `value`
   * while it cannot be measured. `meter` is a level within a range, such as votes against a
   * threshold or draft completeness; give it `valueLabel` ("3 of 5 votes"), which screen
   * readers read instead of a percentage. `thresholds` mark points such as a passing score.
   */
  type Props = {
    label: string;
    kind?: "progress" | "meter";
    value?: number;
    max?: number;
    valueLabel?: string;
    thresholds?: Threshold[];
    tone?: "accent" | "success" | "warning" | "danger";
    labelVisibility?: "visible" | "hidden";
    class?: string;
  };
  let {
    label,
    kind = "progress",
    value,
    max = 100,
    valueLabel,
    thresholds = [],
    tone = "accent",
    labelVisibility = "visible",
    class: className,
  }: Props = $props();
  const id = $props.id();
  const fraction = $derived(value === undefined ? 0 : Math.min(Math.max(value / max, 0), 1));
  const fills = {
    accent: "bg-accent",
    success: "bg-success",
    warning: "bg-warning",
    danger: "bg-danger",
  };
</script>

<div class={cn("grid min-w-0 gap-2 text-sm", className)}>
  <div class={cn("flex justify-between gap-3", labelVisibility === "hidden" && "sr-only")}>
    <span id={`${id}-label`} class="font-semibold">{label}</span>
    {#if valueLabel}<span class="text-fg-muted" aria-hidden="true">{valueLabel}</span>{/if}
  </div>
  {#if kind === "progress"}
    <progress
      class="h-2 w-full overflow-hidden rounded-full bg-surface-muted accent-accent [&::-moz-progress-bar]:bg-accent [&::-webkit-progress-bar]:bg-surface-muted [&::-webkit-progress-value]:bg-accent"
      aria-labelledby={`${id}-label`}
      aria-valuetext={valueLabel}
      {value}
      {max}
    ></progress>
  {:else}
    <div
      class="relative h-2 rounded-full bg-surface-muted"
      role="meter"
      aria-labelledby={`${id}-label`}
      aria-valuenow={value ?? 0}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuetext={valueLabel}
    >
      <!-- eslint-disable svelte/no-inline-styles -- the fill width is data -->
      <div
        class={cn("h-full origin-left rounded-full forced-highlight", fills[tone])}
        style:width={`${fraction * 100}%`}
      ></div>
      {#each thresholds as threshold (threshold.value)}
        <span
          class="absolute -top-1 h-4 -translate-x-1/2 border-s-2 border-fg"
          style:left={`${Math.min(Math.max(threshold.value / max, 0), 1) * 100}%`}
          title={threshold.label}
        ></span>
      {/each}
      <!-- eslint-enable svelte/no-inline-styles -->
    </div>
    {#if thresholds.length > 0}
      <ul class="flex flex-wrap gap-x-4 text-xs text-fg-muted">
        {#each thresholds as threshold (threshold.value)}<li>{threshold.label}</li>{/each}
      </ul>
    {/if}
  {/if}
</div>

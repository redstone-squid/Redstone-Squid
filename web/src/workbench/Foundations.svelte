<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Section from "@/ui/patterns/Section.svelte";

  // Static class names so Tailwind detects them; each swatch shows one theme color.
  const swatches = {
    canvas: "bg-canvas",
    surface: "bg-surface",
    "surface-muted": "bg-surface-muted",
    "surface-raised": "bg-surface-raised",
    fg: "bg-fg",
    "fg-muted": "bg-fg-muted",
    line: "bg-line",
    "line-strong": "bg-line-strong",
    accent: "bg-accent",
    "accent-soft": "bg-accent-soft",
    info: "bg-info",
    "info-soft": "bg-info-soft",
    success: "bg-success",
    "success-soft": "bg-success-soft",
    warning: "bg-warning",
    "warning-soft": "bg-warning-soft",
    danger: "bg-danger",
    "danger-soft": "bg-danger-soft",
    highlight: "bg-highlight",
    focus: "bg-focus",
    "chart-1": "bg-chart-1",
    "chart-2": "bg-chart-2",
    "chart-3": "bg-chart-3",
    "chart-4": "bg-chart-4",
    "chart-5": "bg-chart-5",
    "chart-6": "bg-chart-6",
  };
  const type = [
    ["text-display", "text-display font-bold tracking-display"],
    ["text-3xl", "text-3xl font-bold tracking-tight"],
    ["text-xl", "text-xl font-bold tracking-tight"],
    ["text-lg", "text-lg"],
    ["text-base", "text-base"],
    ["text-sm", "text-sm"],
    ["text-xs", "text-xs"],
    ["font-mono", "font-mono text-base"],
  ] as const;
  const spacing = [
    ["1", "w-1"],
    ["2", "w-2"],
    ["3", "w-3"],
    ["4", "w-4"],
    ["6", "w-6"],
    ["8", "w-8"],
    ["12", "w-12"],
    ["16", "w-16"],
  ] as const;
  const radii = ["rounded-xs", "rounded-sm", "rounded-md", "rounded-lg", "rounded-full"] as const;
</script>

<Section
  id="foundations"
  title={m.workbenchFoundations()}
  description={m.workbenchFoundationsDescription()}
  surface
>
  <ul class="grid grid-cols-[repeat(auto-fill,minmax(8rem,1fr))] gap-3">
    {#each Object.entries(swatches) as [token, background] (token)}
      <li class="grid gap-1">
        <span class={["h-12 rounded-sm border border-line", background]}></span>
        <code class="font-mono text-xs text-fg-muted">--{token}</code>
      </li>
    {/each}
  </ul>
  <div class="grid gap-3 border-t border-line pt-4">
    <h3 class="font-bold">{m.workbenchTypography()}</h3>
    {#each type as [token, sample] (token)}
      <div class="grid gap-1 md:grid-cols-[8rem_minmax(0,1fr)] md:items-baseline">
        <code class="font-mono text-xs text-fg-muted">{token}</code>
        <p class={sample}>{m.workbenchPistonDoor()} 5 × 5 × 3</p>
      </div>
    {/each}
  </div>
  <div class="grid gap-3 border-t border-line pt-4">
    <h3 class="font-bold">{m.workbenchSpacing()}</h3>
    <div class="flex flex-wrap items-end gap-4">
      {#each spacing as [step, width] (step)}
        <div class="grid justify-items-start gap-2">
          <span class={["h-2 rounded-xs bg-accent", width]}></span>
          <code class="font-mono text-xs text-fg-muted">{step}</code>
        </div>
      {/each}
    </div>
    <div class="flex flex-wrap gap-4">
      {#each radii as radius (radius)}
        <div class="grid justify-items-start gap-2">
          <span class={["size-12 border border-line-strong bg-surface-muted", radius]}></span>
          <code class="font-mono text-xs text-fg-muted">{radius}</code>
        </div>
      {/each}
    </div>
  </div>
</Section>

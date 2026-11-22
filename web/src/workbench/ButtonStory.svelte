<script lang="ts">
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import { parseTheme } from "@/ui/themes";
  import { cn } from "@/ui/cn";
  import { buttonStyles, fieldClass, inputClass, layoutStyles } from "@/ui/variants";
  import { selectedOption } from "./story-option";
  import { setStoryOption, storySearch } from "./story-search";

  let { search }: { search: string } = $props();
  const params = $derived($storySearch ?? new URLSearchParams(search));
  const variants = {
    primary: m.workbenchPrimary,
    secondary: m.workbenchSecondary,
    quiet: m.workbenchQuiet,
  } as const;
  const sizes = { normal: m.workbenchNormal, small: m.workbenchSmall } as const;
  const variant = $derived(selectedOption(variants, params, "variant", "primary"));
  const size = $derived(selectedOption(sizes, params, "size", "normal"));
  const trial = $derived(new URLSearchParams({ theme: parseTheme(params.get("theme")) }));
</script>

<div
  class="mb-8 grid grid-cols-[minmax(10rem,0.8fr)_minmax(0,1.2fr)] overflow-hidden rounded-md border border-line max-md:grid-cols-1"
  id="button-story"
>
  <div
    class="grid min-h-36 place-items-center bg-canvas bg-radial-[circle] from-line from-[1px] to-transparent to-[1px] bg-size-[12px_12px] p-4"
  >
    <a class={buttonStyles({ variant, size })} href={localizePath(`/design-system-trial?${trial}`)}>
      {m.trialTitle()} <span aria-hidden="true">→</span>
    </a>
  </div>
  <div class="grid gap-3 p-4">
    <h3>{m.workbenchLinkButton()}</h3>
    <div class={layoutStyles({ pattern: "actions" })}>
      <label class={cn(fieldClass, "font-semibold")}>
        {m.workbenchVariant()}
        <select
          class={inputClass}
          value={variant}
          onchange={(event) => setStoryOption("variant", event.currentTarget.value)}
        >
          {#each Object.entries(variants) as [value, label] (value)}
            <option {value}>{label()}</option>
          {/each}
        </select>
      </label>
      <label class={cn(fieldClass, "font-semibold")}>
        {m.workbenchSize()}
        <select
          class={inputClass}
          value={size}
          onchange={(event) => setStoryOption("size", event.currentTarget.value)}
        >
          {#each Object.entries(sizes) as [value, label] (value)}
            <option {value}>{label()}</option>
          {/each}
        </select>
      </label>
    </div>
    <code class="font-mono text-xs/[1.6] wrap-anywhere text-fg-muted">
      {`buttonStyles({ variant: "${variant}", size: "${size}" })`}
    </code>
  </div>
</div>

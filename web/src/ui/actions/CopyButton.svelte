<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import Copy from "@lucide/svelte/icons/copy";
  import { onDestroy, onMount } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";

  /**
   * Copies `value` to the clipboard, then for two seconds shows `copiedLabel` (or `failedLabel`)
   * and announces it. It stays hidden until hydrated, so put the value beside it in a
   * selectable `Code` for visitors without JavaScript.
   */
  type Props = {
    value: string;
    label: string;
    copiedLabel: string;
    failedLabel: string;
    size?: "sm" | "md";
    iconOnly?: boolean;
    class?: string;
  };
  let {
    value,
    label,
    copiedLabel,
    failedLabel,
    size = "sm",
    iconOnly = false,
    class: className,
  }: Props = $props();

  let hydrated = $state(false);
  let result = $state<"copied" | "failed">();
  let timer: ReturnType<typeof setTimeout> | undefined;
  onMount(() => {
    hydrated = true;
  });
  onDestroy(() => {
    clearTimeout(timer);
  });

  async function copy(): Promise<void> {
    // Copying is idempotent, so a repeat click restarts the feedback instead of being blocked.
    clearTimeout(timer);
    result = undefined;
    try {
      await navigator.clipboard.writeText(value);
      result = "copied";
    } catch {
      result = "failed";
    }
    timer = setTimeout(() => {
      result = undefined;
    }, 2000);
  }
  const message = $derived(
    result === "copied" ? copiedLabel : result === "failed" ? failedLabel : "",
  );
</script>

<Button
  hidden={!hydrated}
  variant="secondary"
  {size}
  {iconOnly}
  {label}
  icon={result === "copied" ? Check : Copy}
  onclick={copy}
  class={className}
>
  <!-- The labels share one grid cell, so the button keeps the width of the longest. The name
       stays `label`; the outcome is announced through the status region. -->
  <span class="grid">
    {#each [[label, undefined], [copiedLabel, "copied"], [failedLabel, "failed"]] as const as [text, state], index (index)}
      <span
        class={cn("col-start-1 row-start-1", result !== state && "opacity-0")}
        aria-hidden={state === undefined ? undefined : "true"}>{text}</span
      >
    {/each}
  </span>
</Button>
<span role="status" class="sr-only">{message}</span>

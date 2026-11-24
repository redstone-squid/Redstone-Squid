<script lang="ts">
  import { cn } from "@/ui/cn";

  /**
   * "n/max" under a text field. Screen readers hear the count only from 90% of the limit,
   * so typing a short value stays quiet.
   */
  let { length, max }: { length: number; max: number } = $props();
  const near = $derived(length >= max * 0.9);
</script>

<span
  class={cn("text-xs tabular-nums", near ? "font-medium text-fg" : "text-fg-muted")}
  aria-hidden="true">{length}/{max}</span
>
<span class="sr-only" role="status">{near ? `${length}/${max}` : ""}</span>

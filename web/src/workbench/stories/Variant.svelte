<script lang="ts">
  import type { Snippet } from "svelte";

  /** A labelled specimen inside a story, such as one variant or state. */
  let { label, children }: { label: string; children: Snippet } = $props();
</script>

<div class="grid min-w-0 content-start gap-2">
  <p class="text-xs font-semibold text-fg-muted">{label}</p>
  {@render children()}
</div>

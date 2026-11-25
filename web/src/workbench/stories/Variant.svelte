<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * A labelled specimen inside a story, such as one variant or state. The caption is monospace
   * so it never reads as part of the specimen, such as a field label. Specimens are independent,
   * so the alignment check (`toHaveAlignedControls`) does not compare controls across them.
   */
  let { label, children }: { label: string; children: Snippet } = $props();
</script>

<div class="grid min-w-0 content-start gap-3" data-specimen>
  <p class="font-mono text-xs text-fg-muted">{label}</p>
  {@render children()}
</div>

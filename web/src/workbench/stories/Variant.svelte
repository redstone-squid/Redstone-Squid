<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * A labelled specimen inside a story, such as one variant or state. The caption is small and
   * regular weight, so it never reads as a field label inside the specimen. Specimens are
   * independent, so the alignment check (`toHaveAlignedControls`) does not compare them.
   */
  let { label, children }: { label: string; children: Snippet } = $props();
</script>

<div class="grid min-w-0 content-start gap-3" data-specimen>
  <p class="text-xs text-fg-muted">{label}</p>
  {@render children()}
</div>

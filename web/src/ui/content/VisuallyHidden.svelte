<script lang="ts">
  import type { Snippet } from "svelte";

  /** Text for screen readers only, such as the context a visible icon gives sighted users. */
  let { children }: { children: Snippet } = $props();
</script>

<span class="sr-only">{@render children()}</span>

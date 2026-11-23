<script lang="ts">
  /** The first focusable element on a page; jumps past the header to `href`. */
  let { href, label }: { href: string; label: string } = $props();
</script>

<a
  {href}
  class="fixed start-4 top-4 z-skiplink rounded-sm bg-surface p-4 font-semibold shadow-overlay not-focus:sr-only"
>
  {label}
</a>

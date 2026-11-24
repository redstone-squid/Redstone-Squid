<script lang="ts">
  import { announcement } from "./announcer.svelte";

  /**
   * The page's one polite live region, which reads messages passed to `announce()`. Render it
   * once, in the layout, as an island; islands on the same page share it.
   */
</script>

<div role="status" class="sr-only">{announcement.message}</div>

<script lang="ts">
  import { onMount } from "svelte";
  import Notice from "@/ui/feedback/Notice.svelte";

  /**
   * The outcome of a form the server handled before redirecting back, such as "Profile
   * saved". The layout reads the one-shot flash and passes its text; render it in the
   * `PageHeader` notice slot. Focus stays where the browser puts it.
   *
   * Screen readers skip a status region that is already filled when the page loads, so once
   * hydrated the notice repeats its text into an empty polite region.
   */
  type Props = { message: string; tone?: "success" | "info" | "warning" | "danger" };
  let { message, tone = "success" }: Props = $props();
  let spoken = $state("");
  onMount(() => {
    const timer = setTimeout(() => (spoken = message), 100);
    return () => clearTimeout(timer);
  });
</script>

<Notice {tone}>{message}</Notice>
<span class="sr-only" role="status">{spoken}</span>

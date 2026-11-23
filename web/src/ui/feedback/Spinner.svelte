<script lang="ts">
  import LoaderCircle from "@lucide/svelte/icons/loader-circle";
  import { cn } from "@/ui/cn";

  /**
   * Work without measurable progress, shown beside one line of text or inside a pending
   * `Button`. Regions that are loading use `Skeleton` instead. Without a `label` the spinner
   * is decorative and the surrounding text says what is happening.
   */
  type Props = { label?: string; size?: "sm" | "md"; class?: string };
  let { label, size = "sm", class: className }: Props = $props();
</script>

<LoaderCircle
  class={cn("shrink-0 animate-spin", size === "sm" ? "size-4" : "size-5", className)}
  aria-hidden={label === undefined ? "true" : undefined}
  role={label === undefined ? undefined : "img"}
  aria-label={label}
/>

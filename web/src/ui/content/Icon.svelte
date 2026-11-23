<script lang="ts">
  import type { LucideIcon } from "@lucide/svelte";
  import { cn } from "@/ui/cn";

  /**
   * A lucide icon sized to the text around it. Icons are decorative unless given a `label`,
   * which makes them an image with that name; prefer visible text next to the icon.
   */
  type Props = { icon: LucideIcon; size?: "sm" | "md" | "lg"; label?: string; class?: string };
  let { icon: Glyph, size = "sm", label, class: className }: Props = $props();
  const sizes = { sm: "size-4", md: "size-5", lg: "size-6" };
</script>

<Glyph
  class={cn("shrink-0", sizes[size], className)}
  aria-hidden={label === undefined ? "true" : undefined}
  role={label === undefined ? undefined : "img"}
  aria-label={label}
/>

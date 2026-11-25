<script lang="ts">
  import { cn } from "@/ui/cn";
  import ModalFrame from "./ModalFrame.svelte";
  import type { ModalProps } from "./props";

  /**
   * A modal panel attached to a screen edge, for filters and navigation on narrow screens. It
   * behaves like `Dialog`; `side` is the edge it slides in from, and `start` and `end` follow
   * the writing direction.
   */
  type Props = ModalProps & { side?: "start" | "end" | "bottom" };
  let { open = $bindable(false), side = "end", ...rest }: Props = $props();
</script>

<ModalFrame
  bind:open
  {...rest}
  panelClass={cn(
    side !== "bottom" && "inset-y-0 w-[calc(100%-3rem)] max-w-dialog-sm",
    // The panel enters from its edge and leaves the same way.
    side === "start" &&
      cn(
        "start-0 rounded-e-lg motion-safe:starting:-translate-x-full rtl:motion-safe:starting:translate-x-full",
        "motion-safe:data-ending-style:-translate-x-full rtl:motion-safe:data-ending-style:translate-x-full",
      ),
    side === "end" &&
      cn(
        "end-0 rounded-s-lg motion-safe:starting:translate-x-full rtl:motion-safe:starting:-translate-x-full",
        "motion-safe:data-ending-style:translate-x-full rtl:motion-safe:data-ending-style:-translate-x-full",
      ),
    side === "bottom" &&
      "inset-x-0 bottom-0 max-h-[85dvh] rounded-t-lg motion-safe:starting:translate-y-full motion-safe:data-ending-style:translate-y-full",
  )}
/>

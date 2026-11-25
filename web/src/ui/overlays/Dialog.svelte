<script lang="ts">
  import { cn } from "@/ui/cn";
  import ModalFrame from "./ModalFrame.svelte";
  import type { ModalProps } from "./props";

  /**
   * A modal task, such as a confirmation or a short form. Focus moves to the first field (or
   * `initialFocus`), stays inside until the dialog closes, then returns to the trigger or to
   * `returnFocus`.
   *
   * With `trigger`, the dialog renders its own button; without it, open it through `open`.
   * Give a trigger `fallbackHref` so the page works before and without hydration. `sm` suits
   * confirmations and `lg` pickers; `full-mobile` rises from the bottom on narrow screens.
   */
  type Props = ModalProps & { size?: "sm" | "md" | "lg" | "full-mobile" };
  let { open = $bindable(false), size = "md", ...rest }: Props = $props();
</script>

<ModalFrame
  bind:open
  {...rest}
  panelClass={cn(
    "inset-0 m-auto h-fit max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] rounded-lg motion-safe:starting:translate-y-2 motion-safe:data-ending-style:translate-y-2",
    size === "sm" && "max-w-dialog-sm",
    (size === "md" || size === "full-mobile") && "max-w-dialog-md",
    size === "lg" && "max-w-dialog-lg",
    size === "full-mobile" &&
      // eslint-disable-next-line better-tailwindcss/no-restricted-classes -- a modal sits in the top layer, so the viewport is its container
      "max-sm:mb-0 max-sm:max-h-[calc(100dvh-1rem)] max-sm:w-full max-sm:max-w-none max-sm:rounded-b-none max-sm:motion-safe:starting:translate-y-full max-sm:motion-safe:data-ending-style:translate-y-full",
  )}
/>

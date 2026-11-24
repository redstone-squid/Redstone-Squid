<script lang="ts">
  import { Tooltip as Primitive } from "bits-ui";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * A short label shown on hover or focus, such as the name of an icon-only button. Render the
   * trigger in `children` and spread the props it receives onto it. The tooltip is a visual aid
   * and needs JavaScript: the trigger must already carry the same text as its accessible name.
   */
  type Props = {
    content: string;
    placement?: "top" | "bottom";
    children: Snippet<[Record<string, unknown>]>;
  };
  let { content, placement = "top", children }: Props = $props();
</script>

<Primitive.Provider delayDuration={400}>
  <Primitive.Root>
    <Primitive.Trigger>
      <!-- The content repeats the trigger's name, so it must not also become its description. -->
      {#snippet child({ props })}{@render children({
          ...props,
          "aria-describedby": undefined,
        })}{/snippet}
    </Primitive.Trigger>
    <Primitive.Portal>
      <Primitive.Content
        side={placement}
        sideOffset={6}
        collisionPadding={8}
        role="tooltip"
        class={cn(
          "z-dropdown max-w-xs rounded-xs border border-transparent bg-fg px-2 py-1 text-xs text-surface shadow-overlay",
          "motion-safe:transition-opacity motion-safe:duration-100 motion-safe:starting:opacity-0",
        )}>{content}</Primitive.Content
      >
    </Primitive.Portal>
  </Primitive.Root>
</Primitive.Provider>

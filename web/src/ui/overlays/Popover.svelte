<script lang="ts">
  import { Popover as Primitive } from "bits-ui";
  import X from "@lucide/svelte/icons/x";
  import type { Snippet } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { floatingPanelClass } from "./styles";
  import { triggerButtonProps, type OverlayTrigger } from "./props";

  /**
   * Non-modal content beside its trigger, such as help for a filter or what a status means.
   * The page stays usable; Escape, an outside click, or the close button dismisses it and
   * focus returns to the trigger. It needs JavaScript, so keep what it says available elsewhere
   * or nonessential.
   */
  type Props = {
    trigger: OverlayTrigger;
    /** The panel's accessible name. */
    label: string;
    closeLabel: string;
    placement?: "top" | "bottom" | "start" | "end";
    open?: boolean;
    children: Snippet;
  };
  let {
    trigger,
    label,
    closeLabel,
    placement = "bottom",
    open = $bindable(false),
    children,
  }: Props = $props();
  const side = $derived(
    ({ top: "top", bottom: "bottom", start: "left", end: "right" } as const)[placement],
  );
</script>

<Primitive.Root bind:open>
  <Primitive.Trigger>
    {#snippet child({ props })}<Button {...triggerButtonProps(trigger)} {...props}
        >{trigger.label}</Button
      >{/snippet}
  </Primitive.Trigger>
  <Primitive.Portal>
    <Primitive.Content
      {side}
      sideOffset={6}
      collisionPadding={16}
      role="dialog"
      aria-label={label}
      class={cn(floatingPanelClass, "flex w-max max-w-[min(22rem,calc(100vw-2rem))] gap-2 p-4")}
    >
      <div class="grid min-w-0 flex-1 content-start gap-2 pt-1">{@render children()}</div>
      <Primitive.Close>
        {#snippet child({ props })}<Button
            {...props}
            variant="quiet"
            size="sm"
            iconOnly
            icon={X}
            label={closeLabel}
            class="-me-2 -mt-2"
          />{/snippet}
      </Primitive.Close>
    </Primitive.Content>
  </Primitive.Portal>
</Primitive.Root>

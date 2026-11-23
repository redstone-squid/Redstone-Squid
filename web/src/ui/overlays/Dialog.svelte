<script lang="ts">
  import { Dialog as Primitive } from "bits-ui";
  import X from "@lucide/svelte/icons/x";
  import type { ComponentProps, Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import Button from "@/ui/actions/Button.svelte";
  type Props = Omit<ComponentProps<typeof Primitive.Root>, "children"> & {
    title: string;
    description: string;
    closeLabel: string;
    trigger: Snippet;
    children: Snippet;
    triggerProps?: Omit<ComponentProps<typeof Primitive.Trigger>, "children" | "child"> & {
      variant?: "primary" | "secondary" | "quiet" | "danger";
      size?: "md" | "sm";
    };
    contentProps?: Omit<ComponentProps<typeof Primitive.Content>, "children" | "child">;
  };
  let {
    title,
    description,
    closeLabel,
    trigger,
    children,
    open = $bindable(false),
    contentProps,
    triggerProps,
    ...rootProps
  }: Props = $props();
  const {
    variant = "primary",
    size,
    class: triggerClass,
    ...nativeTriggerProps
  } = $derived(triggerProps ?? {});
</script>

<Primitive.Root bind:open {...rootProps}>
  <Primitive.Trigger {...nativeTriggerProps}>
    {#snippet child({ props })}<Button {...props} {variant} {size} class={triggerClass}
        >{@render trigger()}</Button
      >{/snippet}
  </Primitive.Trigger>
  <Primitive.Portal>
    <Primitive.Overlay class="fixed inset-0 z-overlay bg-overlay" />
    <Primitive.Content
      {...contentProps}
      class={cn(
        "fixed top-1/2 left-1/2 z-modal grid max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-dialog-md -translate-1/2 gap-6 overflow-auto rounded-lg border border-line bg-surface-raised p-6 text-fg shadow-overlay",
        contentProps?.class,
      )}
    >
      <div class="flex flex-wrap items-center justify-between gap-4">
        <Primitive.Title>
          {#snippet child({ props })}<h2 {...props}>{title}</h2>{/snippet}
        </Primitive.Title>
        <Primitive.Close>
          {#snippet child({ props })}<Button
              {...props}
              variant="quiet"
              iconOnly
              icon={X}
              label={closeLabel}
            />{/snippet}
        </Primitive.Close>
      </div>
      <Primitive.Description class="text-fg-muted">{description}</Primitive.Description>
      {@render children()}
    </Primitive.Content>
  </Primitive.Portal>
</Primitive.Root>

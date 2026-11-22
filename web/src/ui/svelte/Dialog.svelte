<script lang="ts">
  import { Dialog as Primitive } from "bits-ui";
  import X from "@lucide/svelte/icons/x";
  import type { ComponentProps, Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import { buttonStyles, mutedClass, type ButtonVariants } from "@/ui/variants";
  type Props = Omit<ComponentProps<typeof Primitive.Root>, "children"> & {
    title: string;
    description: string;
    closeLabel: string;
    trigger: Snippet;
    children: Snippet;
    triggerProps?: Omit<ComponentProps<typeof Primitive.Trigger>, "children" | "child"> &
      ButtonVariants;
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
  <Primitive.Trigger
    {...nativeTriggerProps}
    class={buttonStyles({ variant, size, className: triggerClass })}
    >{@render trigger()}</Primitive.Trigger
  >
  <Primitive.Portal>
    <Primitive.Overlay class="fixed inset-0 z-50 bg-overlay" />
    <Primitive.Content
      {...contentProps}
      class={cn(
        "fixed top-1/2 left-1/2 z-50 grid max-h-[calc(100dvh-2rem)] w-[min(30rem,calc(100%-2rem))] -translate-1/2 gap-6 overflow-auto rounded-lg border border-line bg-surface p-6 text-fg shadow-card",
        contentProps?.class,
      )}
    >
      <div class="flex flex-wrap items-center justify-between gap-4">
        <Primitive.Title>
          {#snippet child({ props })}<h2 {...props}>{title}</h2>{/snippet}
        </Primitive.Title>
        <Primitive.Close class={buttonStyles({ variant: "quiet" })} aria-label={closeLabel}
          ><X size={18} aria-hidden="true" /></Primitive.Close
        >
      </div>
      <Primitive.Description class={mutedClass}>{description}</Primitive.Description>
      {@render children()}
    </Primitive.Content>
  </Primitive.Portal>
</Primitive.Root>

<script lang="ts">
  import { Dialog as Primitive } from "bits-ui";
  import X from "@lucide/svelte/icons/x";
  import { cx } from "class-variance-authority";
  import type { ComponentProps, Snippet } from "svelte";
  import { buttonStyles, type ButtonVariants } from "../variants";
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
    <Primitive.Overlay class="sq-dialog-overlay" />
    <Primitive.Content {...contentProps} class={cx("sq-dialog", contentProps?.class)}>
      <div class="sq-row sq-between">
        <Primitive.Title>
          {#snippet child({ props })}<h2 {...props}>{title}</h2>{/snippet}
        </Primitive.Title>
        <Primitive.Close class={buttonStyles({ variant: "quiet" })} aria-label={closeLabel}
          ><X size={18} aria-hidden="true" /></Primitive.Close
        >
      </div>
      <Primitive.Description class="sq-muted">{description}</Primitive.Description>
      {@render children()}
    </Primitive.Content>
  </Primitive.Portal>
</Primitive.Root>

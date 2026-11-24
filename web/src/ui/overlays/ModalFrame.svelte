<script lang="ts">
  import { Dialog as Primitive } from "bits-ui";
  import X from "@lucide/svelte/icons/x";
  import { onMount } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { backdropClass } from "./styles";
  import { triggerButtonProps, type ModalProps } from "./props";

  /** The modal shell `Dialog` and `Sheet` share; each passes where its panel sits. */
  type Props = ModalProps & { panelClass: string };
  let {
    open = $bindable(false),
    title,
    description,
    closeLabel,
    trigger,
    fallbackHref,
    children,
    footer,
    dismissible = true,
    initialFocus = "first-field",
    returnFocus,
    onOpenChange,
    panelClass,
  }: Props = $props();

  // Before hydration the trigger links to the fallback page, or is disabled without one, so a
  // click never lands on a button that does nothing.
  let hydrated = $state(false);
  onMount(() => {
    hydrated = true;
  });
  let panel = $state<HTMLElement | null>(null);
  function holdOpen(event: Event): void {
    if (!dismissible) event.preventDefault();
  }

  function focusInitial(event: Event): void {
    const selector =
      initialFocus === "first-field"
        ? "input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled)"
        : initialFocus;
    const target = panel?.querySelector<HTMLElement>(selector);
    // Without a match, bits-ui focuses the dialog itself, so screen readers read its title.
    if (!target) return;
    event.preventDefault();
    target.focus();
  }

  function focusReturn(event: Event): void {
    const target = returnFocus?.();
    if (!target) return;
    event.preventDefault();
    target.focus();
  }
</script>

<Primitive.Root bind:open {onOpenChange}>
  {#if trigger}
    {#if !hydrated && fallbackHref !== undefined}
      <Button {...triggerButtonProps(trigger)} href={fallbackHref}>{trigger.label}</Button>
    {:else}
      <Primitive.Trigger disabled={!hydrated || trigger.disabled === true}>
        {#snippet child({ props })}<Button {...triggerButtonProps(trigger)} {...props}
            >{trigger.label}</Button
          >{/snippet}
      </Primitive.Trigger>
    {/if}
  {/if}
  <Primitive.Portal>
    <Primitive.Overlay class={backdropClass} />
    <Primitive.Content
      bind:ref={panel}
      onInteractOutside={holdOpen}
      onEscapeKeydown={holdOpen}
      onOpenAutoFocus={focusInitial}
      onCloseAutoFocus={focusReturn}
      class={cn(
        "fixed z-modal flex flex-col overflow-hidden border border-line bg-surface-raised text-fg shadow-overlay",
        "motion-safe:transition-[opacity,translate] motion-safe:duration-200 motion-safe:ease-standard motion-safe:starting:opacity-0",
        panelClass,
      )}
    >
      <!-- Divs, not header and footer: portalled to <body>, those would become page landmarks. -->
      <div class="flex items-start justify-between gap-4 p-panel pb-0">
        <div class="grid min-w-0 gap-1 pt-1.5">
          <Primitive.Title>
            {#snippet child({ props })}<h2 {...props} class="text-xl font-bold tracking-tight">
                {title}
              </h2>{/snippet}
          </Primitive.Title>
          {#if description !== undefined}
            <Primitive.Description class="text-sm text-fg-muted"
              >{description}</Primitive.Description
            >
          {/if}
        </div>
        <Primitive.Close disabled={!dismissible}>
          {#snippet child({ props })}<Button
              {...props}
              variant="quiet"
              iconOnly
              icon={X}
              label={closeLabel}
            />{/snippet}
        </Primitive.Close>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto overscroll-contain p-panel">
        {@render children()}
      </div>
      {#if footer}
        <div
          class="flex flex-wrap justify-end gap-3 border-t border-line bg-surface-raised px-panel py-4"
        >
          {@render footer()}
        </div>
      {/if}
    </Primitive.Content>
  </Primitive.Portal>
</Primitive.Root>

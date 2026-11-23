<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import RotateCw from "@lucide/svelte/icons/rotate-cw";
  import { cn } from "@/ui/cn";
  import Button from "@/ui/actions/Button.svelte";
  import Code from "@/ui/content/Code.svelte";

  /**
   * A region that failed to load. Offer a retry, as a link (`retryHref`) when the page itself
   * can be reloaded or a callback (`onRetry`) inside an island. `reference` is the error
   * reference people can quote when reporting the problem.
   */
  type Props = {
    title: string;
    description?: string;
    retryLabel?: string;
    retryHref?: string;
    onRetry?: () => void;
    reference?: { label: string; value: string };
    class?: string;
  };
  let {
    title,
    description,
    retryLabel,
    retryHref,
    onRetry,
    reference,
    class: className,
  }: Props = $props();
</script>

<div
  class={cn(
    "grid justify-items-center gap-2 rounded-md border border-line bg-surface px-4 py-8 text-center",
    className,
  )}
>
  <CircleAlert class="size-7 text-danger" aria-hidden="true" />
  <p class="font-semibold">{title}</p>
  {#if description}<p class="max-w-prose text-sm text-fg-muted">{description}</p>{/if}
  {#if reference}<p class="text-xs text-fg-muted">
      {reference.label}
      <Code>{reference.value}</Code>
    </p>{/if}
  {#if retryLabel !== undefined && (retryHref !== undefined || onRetry)}
    <Button class="mt-2" href={retryHref} onclick={onRetry} icon={RotateCw}>{retryLabel}</Button>
  {/if}
</div>

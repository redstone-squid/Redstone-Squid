<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import { cn } from "@/ui/cn";

  type FieldError = { fieldId: string; message: string };

  /**
   * The problems that stopped a submit, at the top of the form. Each links to its field, and
   * following a link focuses the field so its own error is read. Pass a new `errors` array
   * on each failed submit: with `focusOnMount`, the summary takes focus whenever it changes,
   * which also announces it. Renders nothing when `errors` is empty.
   */
  type Props = {
    title: string;
    errors: FieldError[];
    focusOnMount?: boolean;
    class?: string;
  };
  let { title, errors, focusOnMount = true, class: className }: Props = $props();
  const titleId = $props.id();
  let summary: HTMLElement | undefined = $state();

  $effect(() => {
    if (focusOnMount && errors.length > 0) summary?.focus();
  });

  function jump(event: MouseEvent, fieldId: string): void {
    const field = document.getElementById(fieldId);
    if (!field) return;
    event.preventDefault();
    field.scrollIntoView({ block: "center" });
    field.focus({ preventScroll: true });
  }
</script>

{#if errors.length > 0}
  <div
    bind:this={summary}
    role="alert"
    tabindex="-1"
    aria-labelledby={titleId}
    class={cn(
      "grid min-w-0 gap-2 rounded-sm border-s-4 border-danger bg-danger-soft px-4 py-3 text-sm text-fg",
      className,
    )}
  >
    <p id={titleId} class="flex items-center gap-2 text-base font-semibold">
      <CircleAlert class="size-4.5 shrink-0 text-danger" aria-hidden="true" />{title}
    </p>
    <ul class="grid list-disc gap-1 ps-6.5 marker:text-danger">
      {#each errors as error, index (index)}
        <li>
          <a
            href={`#${error.fieldId}`}
            class="font-medium text-fg hover:text-danger"
            onclick={(event) => jump(event, error.fieldId)}>{error.message}</a
          >
        </li>
      {/each}
    </ul>
  </div>
{/if}

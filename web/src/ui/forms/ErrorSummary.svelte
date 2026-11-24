<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import { cn } from "@/ui/cn";

  type FieldError = { fieldId: string; message: string };

  /**
   * The problems that stopped a submit, at the top of the form. Each links to its field, and
   * following a link focuses the field so its own error is read. Pass a new `errors` array
   * in form order on each failed submit to focus the first available invalid field. Renders nothing when `errors` is empty.
   */
  type Props = {
    title: string;
    errors: FieldError[];
    focusOnMount?: boolean;
    class?: string;
  };
  let { title, errors, focusOnMount = true, class: className }: Props = $props();
  const titleId = $props.id();

  $effect(() => {
    if (!focusOnMount) return;
    for (const { fieldId } of errors) {
      const field = document.getElementById(fieldId);
      field?.focus();
      if (field && document.activeElement === field) break;
    }
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
    role="alert"
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

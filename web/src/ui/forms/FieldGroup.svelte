<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import type { HTMLFieldsetAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import { fieldAttributes, type FieldMessages } from "@/ui/forms/field";
  import { fieldErrorClass, fieldMessageClass, labelClass } from "./styles";

  /**
   * A fieldset naming related fields, such as a door's width, height and depth. Its hint and
   * error describe the whole group; each field keeps its own. `layout="row"` lays the fields
   * side by side and wraps them on narrow screens. `compound` is for a legend that labels one
   * control made of parts, such as radios or a range: it sits as close as a field's label.
   */
  type Props = Omit<HTMLFieldsetAttributes, "width"> &
    Omit<FieldMessages, "width"> & { layout?: "stack" | "row"; compound?: boolean };
  let {
    id,
    label,
    detail,
    hint,
    error,
    labelVisibility = "visible",
    layout = "stack",
    compound = false,
    class: className,
    children,
    ...props
  }: Props = $props();
  const autoId = $props.id();
  const attributes = $derived(fieldAttributes(id ?? autoId, { ...props, hint, error }));
  const hasHint = $derived(hint != null && hint !== "");
</script>

<fieldset
  {...props}
  id={attributes.id}
  aria-describedby={attributes["aria-describedby"]}
  class={cn("grid min-w-0 content-start gap-3 text-sm", className)}
>
  <legend class={cn(labelClass, "mb-1.5", labelVisibility === "hidden" && "sr-only")}
    >{label}{#if detail !== undefined}
      <span class="ms-1 font-normal text-fg-muted">({detail})</span>{/if}</legend
  >
  {#if hasHint}<p id={`${attributes.id}-hint`} class={fieldMessageClass}>
      {hint}
    </p>{/if}
  <!-- Without a hint, the extra space keeps the legend from reading as the first field's label. -->
  <div
    class={cn(
      "min-w-0",
      layout === "row" ? "flex flex-wrap items-start gap-4" : "grid gap-4",
      !hasHint && !compound && "mt-1.5",
    )}
  >
    {@render children?.()}
  </div>
  {#if error != null && error !== ""}<p id={`${attributes.id}-error`} class={fieldErrorClass}>
      <CircleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{error}
    </p>{/if}
</fieldset>

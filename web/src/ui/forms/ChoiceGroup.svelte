<script lang="ts">
  import { cn } from "@/ui/cn";
  import type { ChoiceOption, FieldMessages } from "@/ui/forms/field";
  import FieldGroup from "./FieldGroup.svelte";
  import { checkboxClass } from "./styles";

  /**
   * One choice (`radio`) or several (`checkbox`) from up to about seven options, in a
   * fieldset named by `label`. `value` is a string for radios and a string array for
   * checkboxes. Arrow keys move between radios natively. Use `Combobox` for longer lists.
   *
   * `list` stacks the options, `inline` wraps short ones in a row, and `cards` shows each
   * option as a selectable tile, optionally in fixed `columns` from the small breakpoint.
   */
  type Props = Omit<FieldMessages, "width"> & {
    type?: "radio" | "checkbox";
    name: string;
    options: ChoiceOption[];
    value?: string | string[];
    variant?: "list" | "inline" | "cards";
    columns?: 2 | 3 | 4;
    required?: boolean;
    disabled?: boolean;
    onchange?: (value: string | string[]) => void;
    class?: string;
  };
  let {
    type = "radio",
    name,
    options,
    value = $bindable(type === "radio" ? "" : []),
    variant = "list",
    columns,
    required,
    disabled,
    onchange,
    error,
    hint,
    class: className,
    ...frame
  }: Props = $props();
  const groupId = $props.id();
  const invalid = $derived(error != null && error !== "");
  const columnClass = { 2: "@md:grid-cols-2", 3: "@md:grid-cols-3", 4: "@md:grid-cols-4" };

  const isChecked = (option: string) =>
    Array.isArray(value) ? value.includes(option) : value === option;

  function toggle(option: string, checked: boolean): void {
    if (type === "radio") value = option;
    else {
      const current = Array.isArray(value) ? value : [];
      value = checked
        ? options
            .map((item) => item.value)
            .filter((item) => item === option || current.includes(item))
        : current.filter((item) => item !== option);
    }
    onchange?.(value);
  }
</script>

<FieldGroup {...frame} id={groupId} {hint} {error} class={cn("@container", className)}>
  <div
    class={cn(
      "min-w-0",
      variant === "inline" && "flex flex-wrap gap-x-6 gap-y-3",
      variant === "list" && "grid gap-3",
      variant === "cards" && "grid gap-3",
      variant === "cards" &&
        columns === undefined &&
        "grid-cols-[repeat(auto-fill,minmax(min(100%,var(--container-tile)),1fr))]",
      variant === "cards" && columns !== undefined && columnClass[columns],
    )}
  >
    {#each options as option, index (option.value)}
      {@const optionId = `${groupId}-${index}`}
      <label
        for={optionId}
        class={cn(
          "flex min-w-0 items-start gap-2.5",
          option.disabled === true || disabled === true
            ? "cursor-not-allowed text-fg-muted"
            : "cursor-pointer",
          variant === "cards" && [
            "rounded-md border bg-surface p-4",
            invalid ? "border-danger" : "border-line-strong",
            "transition-[border-color,background-color] duration-150 ease-standard",
            "hover:not-has-disabled:border-fg-muted",
            "has-checked:border-accent has-checked:bg-accent-soft",
            "has-focus-visible:outline-3 has-focus-visible:outline-offset-3 has-focus-visible:outline-focus",
          ],
        )}
      >
        <input
          id={optionId}
          {type}
          {name}
          value={option.value}
          checked={isChecked(option.value)}
          required={type === "radio" ? required : undefined}
          disabled={option.disabled === true || disabled === true}
          aria-invalid={invalid}
          aria-labelledby={`${optionId}-label`}
          aria-describedby={option.description !== undefined
            ? `${optionId}-description`
            : undefined}
          class={cn(checkboxClass, variant === "cards" && "focus-visible:outline-none")}
          onchange={(event) => toggle(option.value, event.currentTarget.checked)}
        />
        <span class="grid min-w-0 gap-0.5">
          <span id={`${optionId}-label`} class="flex items-center gap-2 font-medium">
            {#if option.icon}<option.icon
                class="size-4 shrink-0 text-fg-muted"
                aria-hidden="true"
              />{/if}
            {option.label}
          </span>
          {#if option.description !== undefined}<span
              id={`${optionId}-description`}
              class="text-xs text-fg-muted">{option.description}</span
            >{/if}
        </span>
      </label>
    {/each}
  </div>
</FieldGroup>

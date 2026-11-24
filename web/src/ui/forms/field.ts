import type { LucideIcon } from "@lucide/svelte";

/** The text and layout every field accepts. Optional fields pass `detail: "optional"`. */
export type FieldMessages = {
  label: string;
  detail?: string;
  hint?: string | null;
  error?: string | null;
  /** Hide the label only where a visible heading names the field, such as a table cell. */
  labelVisibility?: "visible" | "hidden";
  /** Size the control to its expected value: `md`, `sm` and `xs` fit 30, 16 and 8 characters. */
  width?: "full" | "md" | "sm" | "xs";
};

type FieldAccessibility = Pick<FieldMessages, "hint" | "error"> & {
  "aria-describedby"?: string | null;
  "aria-invalid"?: boolean | "true" | "false" | "grammar" | "spelling" | null;
};

/** The id, description and invalid state of a control, matching what `FieldFrame` renders. */
export function fieldAttributes(
  id: string,
  { hint, error, "aria-describedby": describedBy, "aria-invalid": invalid }: FieldAccessibility,
) {
  const descriptions = [
    describedBy,
    hint == null || hint === "" ? undefined : `${id}-hint`,
    error == null || error === "" ? undefined : `${id}-error`,
  ].filter(Boolean);
  return {
    id,
    "aria-describedby": descriptions.length > 0 ? descriptions.join(" ") : undefined,
    "aria-invalid": invalid ?? Boolean(error),
  };
}

export type SelectOption = { value: string; label: string; disabled?: boolean };
export type ComboboxOption = SelectOption & { description?: string };
export type ChoiceOption = ComboboxOption & { icon?: LucideIcon };

import { cn } from "@/ui/cn";

// Styles shared by the form controls in this folder. Only src/ui imports them.

export const labelClass = cn("font-semibold text-fg");

const controlBorder = cn(
  "rounded-sm border border-line-strong bg-surface text-fg",
  "transition-[border-color] duration-fast ease-standard",
);
/** A native input, select or textarea. Callers add the inline padding and `min-h-control`. */
export const inputClass = cn(
  controlBorder,
  "min-w-0 py-2 placeholder:text-fg-muted",
  "hover:not-disabled:not-read-only:not-aria-invalid:border-fg-muted aria-invalid:border-danger",
  "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-muted",
  "read-only:bg-surface-muted",
);
/**
 * A box around a borderless input and its adornments (prefix, unit, clear button). The box
 * draws the border, hover, invalid and focus states of the input inside it.
 */
export const boxClass = cn(
  controlBorder,
  "flex min-h-control w-full min-w-0 items-stretch",
  "hover:not-data-disabled:not-has-aria-invalid:border-fg-muted has-aria-invalid:border-danger",
  "has-focus-visible:outline-3 has-focus-visible:outline-offset-3 has-focus-visible:outline-focus",
  "data-disabled:bg-surface-muted data-disabled:text-fg-muted data-readonly:bg-surface-muted",
);
/** The input inside `boxClass`. */
export const boxInputClass = cn(
  "min-w-0 flex-1 bg-transparent px-3 py-2 placeholder:text-fg-muted focus-visible:outline-none",
  "disabled:cursor-not-allowed",
);
/** A native checkbox or radio, aligned with the first line of its label. */
export const checkboxClass = cn(
  "mt-0.5 size-4.5 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed",
);

/** Hint and error text under a control. */
export const fieldMessageClass = cn("text-xs text-fg-muted");
export const fieldErrorClass = cn("flex items-start gap-1.5 text-xs font-medium text-danger");

/** The floating list under a combobox or autocomplete, and one option in it. */
export const popupClass = cn(
  "absolute inset-x-0 top-full z-dropdown mt-1 max-h-72 overflow-y-auto rounded-sm border border-line-strong bg-surface-raised p-1 shadow-overlay",
);
export const popupOptionClass = cn(
  "grid cursor-pointer gap-0.5 rounded-xs px-3 py-2 text-sm wrap-anywhere text-fg",
  "hover:bg-surface-muted aria-selected:forced-highlight aria-selected:bg-accent-soft data-highlighted:forced-highlight data-highlighted:bg-accent-soft",
);

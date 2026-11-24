import { cn } from "@/ui/cn";

// Styles shared by the form controls in this folder. Only src/ui imports them.

/** A label and its control. */
export const fieldClass = cn("grid min-w-0 content-start gap-1.5 text-sm");
export const labelClass = cn("font-semibold text-fg");

const controlBorder = cn(
  "rounded-sm border border-line-strong bg-surface text-fg",
  "transition-[border-color] duration-150 ease-standard",
);
/** A native input, select or textarea. */
export const inputClass = cn(
  controlBorder,
  "min-h-control min-w-0 px-3 py-2 placeholder:text-fg-muted",
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
  "flex min-h-control w-full min-w-0 items-stretch overflow-hidden",
  "hover:not-has-disabled:not-has-aria-invalid:border-fg-muted has-aria-invalid:border-danger",
  "has-focus-visible:outline-3 has-focus-visible:outline-offset-3 has-focus-visible:outline-focus",
  "has-read-only:bg-surface-muted has-disabled:bg-surface-muted has-disabled:text-fg-muted",
);
/** The input inside `boxClass`. */
export const boxInputClass = cn(
  "min-w-0 flex-1 bg-transparent px-3 py-2 placeholder:text-fg-muted focus-visible:outline-none",
  "disabled:cursor-not-allowed",
);
/** Text beside a boxed input, such as a unit. */
export const adornmentClass = cn("flex shrink-0 items-center px-3 text-fg-muted select-none");

export const widthClass = {
  full: "",
  md: "max-w-field-md",
  sm: "max-w-field-sm",
  xs: "max-w-field-xs",
} as const;

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
  "hover:bg-surface-muted aria-selected:bg-accent-soft aria-selected:forced-highlight data-highlighted:bg-accent-soft data-highlighted:forced-highlight",
);

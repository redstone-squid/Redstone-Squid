import { cn } from "@/ui/cn";

// Styles shared by the form controls in this folder. Only src/ui imports them.

/** A label and its control; put `font-semibold` on the label text. */
export const fieldClass = cn("grid min-w-0 content-start gap-2 text-sm");
export const inputClass = cn(
  "min-h-control w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-fg placeholder:text-fg-muted",
  "hover:not-disabled:not-aria-invalid:border-fg-muted aria-invalid:border-danger",
  "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-muted",
  "read-only:bg-surface-muted",
);
/** Hint and error text under a control. */
export const fieldMessageClass = cn("text-xs text-fg-muted");
export const fieldErrorClass = cn("text-xs text-danger");

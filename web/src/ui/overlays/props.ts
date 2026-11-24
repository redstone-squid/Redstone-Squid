import type { LucideIcon } from "@lucide/svelte";
import type { Snippet } from "svelte";

/**
 * The `Button` that opens an overlay. `label` is its text, or its name when `iconOnly`; `name`
 * replaces the accessible name when the text alone is ambiguous.
 */
export type OverlayTrigger = {
  label: string;
  name?: string;
  lang?: string;
  variant?: "primary" | "secondary" | "quiet" | "danger";
  size?: "sm" | "md";
  icon?: LucideIcon;
  iconOnly?: boolean;
  disabled?: boolean;
};

/** The `Button` props for a trigger. */
export function triggerButtonProps({ label, name, ...look }: OverlayTrigger) {
  return { ...look, label: name ?? label, ...(name === undefined ? {} : { "aria-label": name }) };
}

/** Props shared by `Dialog` and `Sheet`. */
export type ModalProps = {
  open?: boolean;
  title: string;
  description?: string;
  closeLabel: string;
  /** Omit for a dialog opened from code through `open`. */
  trigger?: OverlayTrigger;
  /** A page doing the same task, which the trigger links to until the island hydrates. */
  fallbackHref?: string;
  children: Snippet;
  /** Actions pinned below the scrolling body. */
  footer?: Snippet;
  /** False ignores Escape, outside clicks, and the close button, as while saving. */
  dismissible?: boolean;
  /** A selector inside the dialog to focus on open, or its first form field. */
  initialFocus?: "first-field" | (string & {});
  /** Where focus goes on close when the trigger is gone, as after deleting its row. */
  returnFocus?: () => HTMLElement | null | undefined;
  onOpenChange?: (open: boolean) => void;
};

/**
 * One row of a `Menu`. An item runs `onSelect`, follows `href`, or posts a form to `action`.
 * `current` marks the active choice, such as the page's language.
 */
export type MenuItem =
  | {
      kind?: "item";
      label: string;
      href?: string;
      action?: string;
      onSelect?: () => void;
      icon?: LucideIcon;
      danger?: boolean;
      disabled?: boolean;
      current?: boolean;
      lang?: string;
    }
  | {
      kind: "checkbox";
      label: string;
      checked: boolean;
      onCheckedChange: (checked: boolean) => void;
      disabled?: boolean;
    }
  | { kind: "separator" };

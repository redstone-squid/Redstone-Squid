import { cn } from "@/ui/cn";

// Styles shared by the overlays in this folder. Only src/ui imports them.

/** A panel floating beside its trigger: menus and popovers. It fades in where motion is allowed. */
export const floatingPanelClass = cn(
  "z-dropdown max-w-[calc(100vw-2rem)] rounded-sm border border-line bg-surface-raised text-sm text-fg shadow-overlay",
  "motion-safe:transition-opacity motion-safe:duration-fast motion-safe:starting:opacity-0",
);

/** One row of a menu, highlighted by pointer, arrow keys, or focus. */
export const menuItemClass = cn(
  "flex min-h-control-sm w-full cursor-pointer items-center gap-3 rounded-xs px-3 py-2 text-start text-fg no-underline",
  "hover:bg-surface-muted focus-visible:bg-surface-muted data-highlighted:bg-surface-muted",
  "current:forced-highlight current:bg-surface-muted",
  "data-disabled:cursor-not-allowed data-disabled:text-fg-muted",
);

/** The dimmed page behind a modal. */
export const backdropClass = cn(
  "fixed inset-0 z-overlay bg-overlay",
  "motion-safe:transition-opacity motion-safe:duration-normal motion-safe:starting:opacity-0",
);

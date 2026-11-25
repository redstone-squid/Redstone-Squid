import { cn } from "@/ui/cn";

/**
 * A panel floating beside its trigger, shared by Menu, Popover and NavBar's popover. It
 * fades in where motion is allowed. Only src/ui imports it.
 */
export const floatingPanelClass = cn(
  "z-dropdown max-w-[calc(100vw-2rem)] rounded-sm border border-line bg-surface-raised text-sm text-fg shadow-overlay",
  "motion-safe:transition-opacity motion-safe:duration-fast motion-safe:starting:opacity-0",
);

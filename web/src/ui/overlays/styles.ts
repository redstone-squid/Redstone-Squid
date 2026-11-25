import { cn } from "@/ui/cn";

/**
 * A panel floating beside its trigger, shared by Menu, Popover and NavBar's popover. Where
 * motion is allowed it fades in, and fades out while bits-ui marks it `data-ending-style`.
 * A native popover also needs its closed look, such as `not-open:opacity-0`; `display` and
 * `overlay` transition discretely so it stays in the top layer until the fade ends.
 * Only src/ui imports it.
 */
export const floatingPanelClass = cn(
  "z-dropdown max-w-[calc(100vw-2rem)] rounded-sm border border-line bg-surface-raised text-sm text-fg shadow-overlay",
  "motion-safe:transition-[opacity,display,overlay] motion-safe:transition-discrete motion-safe:duration-fast",
  "data-ending-style:opacity-0 motion-safe:starting:opacity-0",
);

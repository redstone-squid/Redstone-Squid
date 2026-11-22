import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/ui/cn";

// Classes are joined, never merged at runtime, so each variant owns the properties it sets:
// the base never sets what a variant changes. Pass a caller `class` only for properties the
// component leaves open, such as margins; lint flags conflicting classes in one string.

export const buttonStyles = cva(
  [
    "inline-flex cursor-pointer items-center justify-center gap-2 rounded-sm border",
    "text-sm font-semibold no-underline transition-[background-color] duration-140",
    "active:not-disabled:shadow-pressed",
    "disabled:cursor-not-allowed disabled:border-line disabled:bg-surface-muted disabled:text-fg-muted",
  ],
  {
    variants: {
      variant: {
        primary:
          "border-accent bg-accent text-on-accent hover:not-disabled:border-accent-hover hover:not-disabled:bg-accent-hover",
        secondary: "border-line bg-surface text-fg hover:not-disabled:bg-surface-muted",
        quiet: "border-transparent bg-transparent text-fg hover:not-disabled:bg-surface-muted",
      },
      size: { normal: "min-h-11 px-4 py-2.5", small: "min-h-9 px-3 py-1.5" },
    },
    defaultVariants: { variant: "secondary", size: "normal" },
  },
);
export type ButtonVariants = VariantProps<typeof buttonStyles>;

const layoutPatterns = cva("min-w-0", {
  variants: {
    pattern: {
      stack: "grid",
      row: "flex flex-wrap items-center",
      actions: "flex flex-wrap items-end",
      grid: "grid grid-cols-[repeat(auto-fit,minmax(min(100%,16rem),1fr))]",
      container: "mx-auto grid w-full max-w-content px-[clamp(1rem,4vw,3rem)]",
    },
    gap: {
      none: "",
      compact: "gap-2",
      snug: "gap-3",
      normal: "gap-4",
      loose: "gap-6",
      section: "gap-8",
    },
  },
});
type LayoutPattern = NonNullable<VariantProps<typeof layoutPatterns>["pattern"]>;
type LayoutGap = NonNullable<VariantProps<typeof layoutPatterns>["gap"]>;
const PATTERN_GAPS: Record<LayoutPattern, LayoutGap> = {
  stack: "loose",
  row: "snug",
  actions: "snug",
  grid: "normal",
  container: "none",
};
export type LayoutVariants = { pattern?: LayoutPattern; gap?: LayoutGap };

/** Arrange children; each pattern has a default gap that `gap` replaces. */
export function layoutStyles({
  pattern = "stack",
  gap,
  className,
}: LayoutVariants & { className?: string } = {}): string {
  return cn(layoutPatterns({ pattern, gap: gap ?? PATTERN_GAPS[pattern] }), className);
}

export const noticeStyles = cva(
  "grid min-w-0 gap-1 rounded-sm border-s-[0.2rem] px-4 py-3 text-sm wrap-anywhere text-fg",
  {
    variants: {
      tone: {
        info: "border-line bg-surface-muted",
        success: "border-success bg-success-soft",
        warning: "border-warning bg-warning-soft",
        danger: "border-danger bg-accent-soft",
      },
    },
    defaultVariants: { tone: "info" },
  },
);
export type NoticeProps = VariantProps<typeof noticeStyles> & {
  title?: string;
  role?: "status" | "alert";
};

// Shared classes for native markup that has no component. `cn` only joins strings here; it
// marks the strings for the Tailwind lint rules.
export const mutedClass = cn("text-fg-muted");
export const kickerClass = cn("mb-3 text-sm leading-[normal] font-semibold text-fg-muted");
export const panelClass = cn(
  "min-w-0 rounded-lg border border-line bg-surface p-6 shadow-card max-md:p-4",
);
/** A label and its control; put `font-semibold` on the label text. */
export const fieldClass = cn("grid min-w-0 content-start gap-2 text-sm");
export const inputClass = cn(
  "min-h-11 w-full rounded-sm border border-line bg-surface px-3 py-2 text-fg placeholder:text-fg-muted",
  "hover:not-disabled:not-aria-invalid:border-fg-muted aria-invalid:border-danger",
  "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-muted",
);

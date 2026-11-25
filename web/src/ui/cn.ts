import { cx } from "class-variance-authority";

/**
 * Join class names, skipping falsy values. Nothing is merged at runtime, which would ship a
 * class resolver to every island; components avoid conflicts instead (see src/ui/README.md).
 */
export const cn = cx;

import type { LucideIcon } from "@lucide/svelte";

/**
 * One link in `RelatedLinks`: a `label` followed by its formatted `count`, such as "records held"
 * and "3". The caller formats both, so the label can agree with the count.
 */
export type RelatedLink = { label: string; count: string; href: string; icon?: LucideIcon };

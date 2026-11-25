import type { LucideIcon } from "@lucide/svelte";

/**
 * One link in `RelatedLinks`: a formatted `count` shown above its `label`, such as "3" and
 * "records held". The caller formats both, so the label can agree with the count.
 */
export type RelatedLink = { label: string; count: string; href: string; icon?: LucideIcon };

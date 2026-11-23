<script lang="ts">
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import type { Snippet } from "svelte";
  import type { HTMLAnchorAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import VisuallyHidden from "./VisuallyHidden.svelte";

  /**
   * A text link. `inline` sits in running text and stays underlined; `standalone` is a link on
   * its own line; `subtle` is metadata. `externalLabel` marks a link to another site with an
   * icon and that hidden text, such as "opens another site".
   */
  type Props = HTMLAnchorAttributes & {
    href: string;
    variant?: "inline" | "standalone" | "subtle";
    externalLabel?: string;
    current?: boolean;
    children: Snippet;
  };
  let {
    href,
    variant = "inline",
    externalLabel,
    current = false,
    class: className,
    children,
    ...rest
  }: Props = $props();
  const variants = {
    inline: "text-fg underline",
    standalone: "font-semibold text-fg no-underline hover:underline",
    subtle: "text-fg-muted no-underline hover:text-fg hover:underline",
  };
</script>

<a
  {href}
  class={cn("rounded-xs underline-offset-[0.2em]", variants[variant], className)}
  aria-current={current ? "page" : undefined}
  rel={externalLabel === undefined ? undefined : "noreferrer"}
  {...rest}
  >{@render children()}{#if externalLabel !== undefined}<ExternalLink
      class="ms-1 inline size-[0.9em] align-[-0.1em]"
      aria-hidden="true"
    /><VisuallyHidden>{` (${externalLabel})`}</VisuallyHidden>{/if}</a
>

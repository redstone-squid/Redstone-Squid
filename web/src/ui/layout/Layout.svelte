<script lang="ts" module>
  import { cva, type VariantProps } from "class-variance-authority";

  const layoutStyles = cva("min-w-0", {
    variants: {
      pattern: {
        stack: "grid content-start",
        cluster: "flex flex-wrap",
        split: "flex flex-wrap",
        grid: "grid",
        center: "grid place-content-center",
      },
      min: {
        none: "",
        tile: "grid-cols-[repeat(auto-fill,minmax(min(100%,var(--container-tile)),1fr))]",
        card: "grid-cols-[repeat(auto-fill,minmax(min(100%,var(--container-card)),1fr))]",
        "card-wide":
          "grid-cols-[repeat(auto-fill,minmax(min(100%,var(--container-card-wide)),1fr))]",
      },
      gap: {
        none: "",
        "2xs": "gap-1",
        xs: "gap-2",
        sm: "gap-3",
        md: "gap-4",
        lg: "gap-6",
        xl: "gap-8",
      },
      align: {
        none: "",
        start: "items-start",
        center: "items-center",
        end: "items-end",
        baseline: "items-baseline",
        stretch: "items-stretch",
      },
      justify: { none: "", start: "justify-start", end: "justify-end", between: "justify-between" },
      list: { true: "list-none", false: "" },
    },
  });
  type Variants = VariantProps<typeof layoutStyles>;
  type Pattern = NonNullable<Variants["pattern"]>;
  type Options = {
    gap?: NonNullable<Variants["gap"]>;
    min?: Exclude<NonNullable<Variants["min"]>, "none">;
    align?: Exclude<NonNullable<Variants["align"]>, "none">;
    justify?: Exclude<NonNullable<Variants["justify"]>, "none">;
  };
  const PATTERN_DEFAULTS = {
    stack: { gap: "lg" },
    cluster: { gap: "sm", align: "center" },
    split: { gap: "md", align: "center", justify: "between" },
    grid: { gap: "md", min: "card" },
    center: { gap: "none" },
  } as const satisfies Record<Pattern, Options>;
</script>

<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";

  /**
   * Arrange children: `stack` (vertical rhythm), `cluster` (wrapping row), `split` (spread
   * apart, such as a title and its actions), `grid` (responsive columns at least `min` wide),
   * or `center`. Each pattern has defaults that the other options replace. Use `as="ul"` for
   * a list of cards so each child is an `li`.
   */
  type Props = HTMLAttributes<HTMLElement> &
    Options & {
      pattern?: Pattern;
      as?: "div" | "section" | "article" | "header" | "footer" | "nav" | "ul" | "ol" | "li";
      children?: Snippet;
    };
  let {
    as = "div",
    pattern = "stack",
    gap,
    min,
    align,
    justify,
    class: className,
    children,
    ...rest
  }: Props = $props();
  const defaults: Options = $derived(PATTERN_DEFAULTS[pattern]);
</script>

<svelte:element
  this={as}
  class={cn(
    layoutStyles({
      pattern,
      gap: gap ?? defaults.gap ?? "none",
      min: pattern === "grid" ? (min ?? defaults.min) : "none",
      align: align ?? defaults.align ?? "none",
      justify: justify ?? defaults.justify ?? "none",
      list: as === "ul" || as === "ol",
    }),
    className,
  )}
  {...rest}
>
  {@render children?.()}
</svelte:element>

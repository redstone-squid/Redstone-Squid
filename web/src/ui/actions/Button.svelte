<script lang="ts" module>
  import { cva, type VariantProps } from "class-variance-authority";

  const buttonStyles = cva(
    [
      "relative inline-flex cursor-pointer items-center justify-center gap-2 rounded-sm border",
      "font-semibold no-underline transition-[background-color,border-color] duration-150 ease-standard",
      "active:not-disabled:not-aria-disabled:shadow-pressed",
      "disabled:cursor-not-allowed disabled:border-line disabled:bg-surface-muted disabled:text-fg-muted",
      "aria-disabled:cursor-not-allowed",
      "data-disabled:border-line data-disabled:bg-surface-muted data-disabled:text-fg-muted",
    ],
    {
      variants: {
        variant: {
          primary:
            "border-accent bg-accent text-on-accent hover:not-disabled:not-aria-disabled:border-accent-hover hover:not-disabled:not-aria-disabled:bg-accent-hover",
          secondary:
            "border-line-strong bg-surface text-fg hover:not-disabled:not-aria-disabled:bg-surface-muted",
          quiet:
            "border-transparent bg-transparent text-fg hover:not-disabled:not-aria-disabled:bg-surface-muted",
          danger:
            "border-danger bg-danger text-on-danger hover:not-disabled:not-aria-disabled:opacity-90",
        },
        size: {
          md: "min-h-control px-4 py-2.5 text-sm",
          sm: "min-h-control-sm px-3 py-1.5 text-sm",
        },
        iconOnly: { true: "aspect-square px-0 py-0", false: "" },
      },
      compoundVariants: [
        { iconOnly: true, size: "md", class: "w-control" },
        { iconOnly: true, size: "sm", class: "w-control-sm" },
      ],
      defaultVariants: { variant: "secondary", size: "md", iconOnly: false },
    },
  );
  type ButtonVariants = Pick<VariantProps<typeof buttonStyles>, "variant" | "size">;
</script>

<script lang="ts">
  import type { LucideIcon } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import type { HTMLAnchorAttributes, HTMLButtonAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import Spinner from "@/ui/feedback/Spinner.svelte";

  /**
   * A command, or with `href` a link styled as one. Use it for navigation that reads as an
   * action, such as "Submit a build"; use `Link` inside text.
   *
   * `pending` keeps the button focusable but ignores clicks and form submission, swaps the
   * start icon for a spinner, and announces `pendingLabel`. A disabled link renders without
   * an `href`. `iconOnly` hides the text and requires `label`, which becomes its name.
   */
  type Props = ButtonVariants &
    Omit<HTMLButtonAttributes, "children"> &
    Omit<HTMLAnchorAttributes, keyof HTMLButtonAttributes | "children"> & {
      href?: string;
      icon?: LucideIcon;
      iconEnd?: LucideIcon;
      iconOnly?: boolean;
      label?: string;
      pending?: boolean;
      pendingLabel?: string;
      fullWidth?: boolean;
      children?: Snippet;
    };
  let {
    href,
    variant,
    size = "md",
    icon: StartIcon,
    iconEnd: EndIcon,
    iconOnly = false,
    label,
    pending = false,
    pendingLabel,
    fullWidth = false,
    disabled = false,
    type = "button",
    class: className,
    onclick,
    children,
    ...rest
  }: Props = $props();
  const classes = $derived(
    buttonStyles({
      variant,
      size,
      iconOnly,
      className: cn(fullWidth && "w-full", className),
    }),
  );
  const iconClass = $derived(size === "sm" ? "size-4" : "size-[1.125rem]");
</script>

{#snippet content()}
  {#if pending}<Spinner />{:else if StartIcon}<StartIcon
      class={cn("shrink-0", iconClass)}
      aria-hidden="true"
    />{/if}{#if !iconOnly}{@render children?.()}{/if}{#if EndIcon}<EndIcon
      class={cn("shrink-0", iconClass)}
      aria-hidden="true"
    />{/if}
{/snippet}

{#if href !== undefined}
  <a
    href={disabled ? undefined : href}
    role={disabled ? "link" : undefined}
    aria-disabled={disabled ? "true" : undefined}
    data-disabled={disabled ? "" : undefined}
    aria-label={iconOnly ? label : undefined}
    class={classes}
    onclick={onclick as HTMLAnchorAttributes["onclick"]}
    {...rest as HTMLAnchorAttributes}>{@render content()}</a
  >
{:else}
  <button
    {type}
    {disabled}
    aria-disabled={pending ? "true" : undefined}
    aria-label={iconOnly ? label : undefined}
    class={classes}
    onclick={(event) => {
      if (pending) event.preventDefault();
      else onclick?.(event);
    }}
    {...rest}>{@render content()}</button
  >
{/if}
{#if pendingLabel !== undefined}<span class="sr-only" role="status"
    >{pending ? pendingLabel : ""}</span
  >{/if}

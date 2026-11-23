<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import Info from "@lucide/svelte/icons/info";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * A message about a whole page, region, or operation, with optional recovery `actions`. Put
   * an error the user can fix in one field on that field instead.
   *
   * `role` decides how it is announced: `status` for outcomes (saved, sent), `alert` for
   * failures that need attention now, and none for content present when the page loads.
   * Keep one live notice per region. `banner` spans the top of a page.
   */
  type Props = {
    tone?: "info" | "success" | "warning" | "danger";
    title?: string;
    role?: "status" | "alert";
    variant?: "inline" | "banner";
    actions?: Snippet;
    class?: string;
    children?: Snippet;
  };
  let {
    tone = "info",
    title,
    role,
    variant = "inline",
    actions,
    class: className,
    children,
  }: Props = $props();
  const tones = {
    info: { class: "border-info bg-info-soft", icon: Info, iconClass: "text-info" },
    success: {
      class: "border-success bg-success-soft",
      icon: CircleCheck,
      iconClass: "text-success",
    },
    warning: {
      class: "border-warning bg-warning-soft",
      icon: TriangleAlert,
      iconClass: "text-warning",
    },
    danger: { class: "border-danger bg-danger-soft", icon: CircleAlert, iconClass: "text-danger" },
  };
  const style = $derived(tones[tone]);
</script>

<div
  class={cn(
    "flex min-w-0 gap-3 text-sm text-fg",
    variant === "banner" ? "border-y px-gutter py-3" : "rounded-sm border-s-4 px-4 py-3",
    style.class,
    className,
  )}
  {role}
>
  <style.icon class={cn("mt-0.5 size-[1.125rem] shrink-0", style.iconClass)} aria-hidden="true" />
  <div class="grid min-w-0 flex-1 gap-1 wrap-break-word">
    {#if title}<strong class="font-semibold">{title}</strong>{/if}
    {@render children?.()}
    {#if actions}<div class="mt-2 flex flex-wrap items-center gap-3">{@render actions()}</div>{/if}
  </div>
</div>

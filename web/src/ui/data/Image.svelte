<script lang="ts">
  import ImageOff from "@lucide/svelte/icons/image-off";
  import type { LucideIcon } from "@lucide/svelte";
  import { cn } from "@/ui/cn";

  /**
   * An image in a fixed frame, so the page does not shift while it loads. When `src` is
   * missing or fails to load, the frame shows `fallbackLabel`. Callers decide which URLs are
   * safe to show; pass only HTTPS URLs from untrusted data. `width` and `height` are the
   * image's intrinsic size and set the frame's ratio when `aspect` is `natural`. Set `priority`
   * on the page's main image above the fold (at most one or two per page): it loads immediately
   * at high priority instead of lazily.
   *
   * `sources` offers the image at several widths. Lazy images let the browser pick from the
   * width the frame actually renders at (`sizes="auto"`), so `sources.sizes` only matters for a
   * `priority` image, which loads before layout, and for browsers without `sizes="auto"`.
   */
  type Props = {
    src: string | null;
    alt: string;
    width: number;
    height: number;
    aspect?: "wide" | "square" | "video" | "natural";
    fit?: "cover" | "contain";
    priority?: boolean;
    sources?: { srcset: string; sizes: string };
    fallbackLabel: string;
    fallbackIcon?: LucideIcon;
    class?: string;
  };
  let {
    src,
    alt,
    width,
    height,
    aspect = "wide",
    fit = "cover",
    priority = false,
    sources,
    fallbackLabel,
    fallbackIcon: Glyph = ImageOff,
    class: className,
  }: Props = $props();
  const aspects = {
    wide: "aspect-8/5",
    square: "aspect-square",
    video: "aspect-video",
    natural: "",
  };
</script>

<!-- eslint-disable svelte/no-inline-styles -- a natural frame takes the image's own ratio -->
<div
  class={cn("group relative overflow-hidden bg-surface-muted", aspects[aspect], className)}
  style:aspect-ratio={aspect === "natural" ? `${width} / ${height}` : undefined}
>
  <div
    class={cn(
      "absolute inset-0 grid content-center justify-items-center gap-2 p-4 text-center text-xs text-fg-muted",
      src !== null && "invisible group-has-[>img[hidden]]:visible",
    )}
  >
    <Glyph class="size-8" aria-hidden="true" />
    {fallbackLabel}
  </div>
  <!-- eslint-enable svelte/no-inline-styles -->
  {#if src !== null}
    <!-- The browser fetches when src is set, so the attributes that choose the request come first. -->
    <img
      loading={priority ? "eager" : "lazy"}
      fetchpriority={priority ? "high" : undefined}
      decoding="async"
      referrerpolicy="no-referrer"
      sizes={sources && (priority ? sources.sizes : `auto, ${sources.sizes}`)}
      srcset={sources?.srcset}
      {src}
      {width}
      {height}
      {alt}
      data-fallback
      class={cn("absolute inset-0 size-full", fit === "cover" ? "object-cover" : "object-contain")}
      onerror={(event) => {
        (event.currentTarget as HTMLImageElement).hidden = true;
      }}
    />
  {/if}
</div>

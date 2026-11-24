<script lang="ts">
  import { cn } from "@/ui/cn";
  import { hideWhenBroken } from "./hide-when-broken";

  /**
   * A person's picture, falling back to their initials when there is none or it fails to
   * load. Decorative by default because the name is usually shown beside it; pass `alt` when
   * the avatar stands alone.
   */
  type Props = {
    src?: string | null;
    name: string;
    alt?: string;
    size?: "sm" | "md" | "lg";
    class?: string;
  };
  let { src = null, name, alt = "", size = "md", class: className }: Props = $props();
  const sizes = { sm: "size-6 text-xs", md: "size-10 text-sm", lg: "size-20 text-2xl" };
  const pixels = { sm: 24, md: 40, lg: 80 };
  // Latin names give two initials; a CJK name's first character already identifies it.
  const initials = $derived(
    name
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => Array.from(word)[0] ?? "")
      .join("")
      .toUpperCase(),
  );
</script>

<span
  class={cn(
    "group relative inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-accent-soft font-semibold text-accent",
    sizes[size],
    className,
  )}
  role={alt === "" ? undefined : "img"}
  aria-label={alt === "" ? undefined : alt}
>
  <span aria-hidden="true">{initials}</span>
  {#if src !== null}
    <img
      {src}
      alt=""
      width={pixels[size]}
      height={pixels[size]}
      loading="lazy"
      decoding="async"
      referrerpolicy="no-referrer"
      data-fallback
      {@attach hideWhenBroken}
      class="absolute inset-0 size-full object-cover"
    />
  {/if}
</span>

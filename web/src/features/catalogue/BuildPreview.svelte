<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import Image from "@/ui/data/Image.svelte";
  let { build, priority = false }: { build: PublicBuildSummary; priority?: boolean } = $props();
  const title = $derived(build.display_name ?? build.title);
  // Only HTTPS and same-origin previews are shown; the API should never send others, but this
  // is untrusted input.
  const preview = $derived.by(() => {
    const url = build.preview?.url;
    if (url === undefined) return null;
    if (url.startsWith("/") && !url.startsWith("//")) return url;
    return URL.canParse(url) && url.startsWith("https://") ? url : null;
  });
</script>

<Image
  src={preview}
  alt={m.buildPreviewAlt({ title })}
  width={640}
  height={400}
  {priority}
  fallbackLabel={preview === null ? m.buildNoPreview() : m.buildImageUnavailable()}
/>

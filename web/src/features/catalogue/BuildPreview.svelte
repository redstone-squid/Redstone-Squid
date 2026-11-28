<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Image from "@/ui/data/Image.svelte";
  import { buildName, previewUrl, type BuildIdentity } from "./presentation";

  /**
   * A build's preview image, or a placeholder when it has none or it fails to load. A
   * `thumbnail` is a small `square` or `wide` frame whose placeholder shows only its icon.
   */
  type Props = {
    build: Pick<BuildIdentity, "title" | "display_name" | "preview">;
    priority?: boolean;
    thumbnail?: "square" | "wide";
    class?: string;
  };
  let { build, priority = false, thumbnail, class: className }: Props = $props();
  const src = $derived(previewUrl(build.preview));
</script>

<Image
  {src}
  alt={m.buildPreviewAlt({ title: buildName(build) })}
  width={640}
  height={400}
  aspect={thumbnail ?? "wide"}
  fallback={thumbnail === undefined ? "label" : "icon"}
  {priority}
  fallbackLabel={src === null ? m.buildNoPreview() : m.buildImageUnavailable()}
  class={className}
/>

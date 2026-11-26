<script lang="ts">
  import Box from "@lucide/svelte/icons/box";
  import type { SchematicRenderer } from "schematic-renderer";
  import { onDestroy, onMount, tick } from "svelte";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import Text from "@/ui/content/Text.svelte";
  import Image from "@/ui/data/Image.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import Layout from "@/ui/layout/Layout.svelte";

  /**
   * A schematic shown as its preview image until the visitor opens it in 3D. The renderer
   * weighs several megabytes, so it downloads only then; without JavaScript the preview stays.
   * `resourcePackUrl` supplies the textures, normally the API's `/v1/schematic-resource-pack`.
   * The renderer draws nothing without them, so a missing pack fails like a missing schematic.
   */
  type Props = {
    title: string;
    schematicUrl: string;
    previewUrl: string | null;
    resourcePackUrl: string;
  };
  let { title, schematicUrl, previewUrl, resourcePackUrl }: Props = $props();

  let status = $state<"closed" | "loading" | "ready" | "failed">("closed");
  let hydrated = $state(false);
  let canvas = $state<HTMLCanvasElement>();
  let renderer: SchematicRenderer | undefined;
  onMount(() => (hydrated = true));
  onDestroy(close);

  function close(): void {
    renderer?.dispose();
    renderer = undefined;
  }

  async function download(url: string): Promise<Response> {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} answered ${response.status}`);
    return response;
  }

  async function open(): Promise<void> {
    close();
    status = "loading";
    try {
      const [{ SchematicRenderer }, schematic, pack] = await Promise.all([
        import("schematic-renderer"),
        download(schematicUrl).then((response) => response.arrayBuffer()),
        download(resourcePackUrl).then((response) => response.blob()),
      ]);
      // The canvas renders with the loading state; it is gone if the viewer was destroyed.
      await tick();
      if (canvas === undefined) return;
      renderer = new SchematicRenderer(
        canvas,
        { schematic: () => Promise.resolve(schematic) },
        { textures: () => Promise.resolve(pack) },
        {
          singleSchematicMode: true,
          enableProgressBar: false,
          sidebarOptions: { enabled: false },
          resourcePackOptions: { enableUI: false, showMissingPackNotice: false },
          definitionRegionOptions: { showOnLoad: false },
          callbacks: {
            onSchematicRendered: () => {
              status = "ready";
              // The renderer draws on demand and would otherwise wait for the first pointer move.
              renderer?.invalidate();
            },
          },
        },
      );
    } catch {
      close();
      status = "failed";
    }
  }
</script>

<Layout gap="sm">
  {#if status === "closed"}
    <Image
      src={previewUrl}
      alt={m.buildPreviewAlt({ title })}
      width={640}
      height={400}
      fallbackLabel={m.buildNoPreview()}
    />
  {:else}
    <div
      class="relative aspect-8/5 overflow-hidden rounded-md bg-surface-muted"
      role="group"
      aria-label={m.schematicViewerLabel({ title })}
    >
      <canvas
        bind:this={canvas}
        class={cn("absolute inset-0 size-full touch-none", status !== "ready" && "invisible")}
      ></canvas>
      {#if status === "loading"}
        <div class="absolute inset-0 grid place-items-center">
          <Spinner size="md" label={m.schematicViewerLoading()} />
        </div>
      {/if}
    </div>
  {/if}
  {#if status === "failed"}
    <Notice tone="danger" role="alert" title={m.schematicViewerFailed()}>
      {#snippet actions()}
        <Button size="sm" variant="secondary" onclick={open}>{m.schematicViewerRetry()}</Button>
      {/snippet}
    </Notice>
  {:else if status === "ready"}
    <Text size="sm" tone="muted">{m.schematicViewerHelp()}</Text>
  {:else if status === "closed" && hydrated}
    <div>
      <Button variant="secondary" icon={Box} onclick={open}>{m.schematicViewerOpen()}</Button>
    </div>
  {/if}
</Layout>

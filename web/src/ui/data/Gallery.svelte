<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Maximize from "@lucide/svelte/icons/maximize-2";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import Dialog from "@/ui/overlays/Dialog.svelte";
  import Image from "./Image.svelte";

  type Item = {
    kind: "image" | "video" | "embed";
    src: string;
    thumb: string;
    width: number;
    height: number;
    alt: string;
    caption?: string;
  };

  /**
   * A main viewer over a strip of thumbnails, with a full-size lightbox. Without JavaScript the
   * first item shows and each thumbnail links to its full file; hydrated, a thumbnail switches
   * the viewer and arrow keys page through the lightbox. Videos play in native controls and
   * embeds, such as a schematic viewer, load in a frame titled by `alt`.
   */
  type Props = {
    items: Item[];
    labels: {
      previous: string;
      next: string;
      close: string;
      openItem: string;
      fallback: string;
      position: (current: number, total: number) => string;
    };
    class?: string;
  };
  let { items, labels, class: className }: Props = $props();

  let index = $state(0);
  let open = $state(false);
  const current = $derived(items[index] ?? items[0]);

  function show(next: number) {
    index = (next + items.length) % items.length;
  }
</script>

<!-- Arrow keys page through the items while the lightbox is open, wherever focus sits in it. -->
<svelte:document
  onkeydown={(event) => {
    if (!open) return;
    if (event.key === "ArrowRight") show(index + 1);
    if (event.key === "ArrowLeft") show(index - 1);
  }}
/>

{#snippet media(item: Item, eager: boolean)}
  {#if item.kind === "image"}
    <Image
      src={item.src}
      alt={item.alt}
      width={item.width}
      height={item.height}
      aspect="video"
      fit="contain"
      priority={eager}
      fallbackLabel={labels.fallback}
      class="rounded-md"
    />
  {:else if item.kind === "video"}
    <!-- Build showcases have no speech to caption. -->
    <!-- svelte-ignore a11y_media_has_caption -->
    <video
      src={item.src}
      poster={item.thumb}
      width={item.width}
      height={item.height}
      controls
      preload="metadata"
      aria-label={item.alt}
      class="aspect-video w-full rounded-md bg-surface-muted object-contain"
    ></video>
  {:else}
    <iframe
      src={item.src}
      title={item.alt}
      loading="lazy"
      referrerpolicy="no-referrer"
      class="aspect-video w-full rounded-md border border-line bg-surface-muted"
    ></iframe>
  {/if}
{/snippet}

{#if current !== undefined}
  <div class={cn("grid min-w-0 gap-3", className)}>
    <figure class="relative grid gap-2">
      {@render media(current, true)}
      {#if current.caption !== undefined}
        <figcaption class="text-sm text-fg-muted">{current.caption}</figcaption>
      {/if}
      <div class="absolute top-2 right-2">
        <Dialog
          bind:open
          size="lg"
          title={labels.position(index + 1, items.length)}
          description={current.caption ?? current.alt}
          closeLabel={labels.close}
          fallbackHref={current.src}
          trigger={{
            label: labels.openItem,
            icon: Maximize,
            iconOnly: true,
            variant: "secondary",
            size: "sm",
          }}
        >
          <div class="grid gap-3">
            {@render media(current, true)}
            {#if items.length > 1}
              <div class="flex items-center justify-between gap-3">
                <Button size="sm" icon={ChevronLeft} onclick={() => show(index - 1)}
                  >{labels.previous}</Button
                >
                <Button size="sm" iconEnd={ChevronRight} onclick={() => show(index + 1)}
                  >{labels.next}</Button
                >
              </div>
            {/if}
          </div>
        </Dialog>
      </div>
    </figure>
    {#if items.length > 1}
      <ul class="flex gap-2 overflow-x-auto pb-1">
        {#each items as item, itemIndex (item.src)}
          <li class="shrink-0">
            <a
              href={item.src}
              aria-current={itemIndex === index ? "true" : undefined}
              class={cn(
                "block w-24 overflow-hidden rounded-sm border-2 no-underline",
                itemIndex === index
                  ? "border-accent"
                  : "border-transparent hover:border-line-strong",
              )}
              onclick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                event.preventDefault();
                show(itemIndex);
              }}
            >
              <Image
                src={item.thumb}
                alt={item.alt}
                width={item.width}
                height={item.height}
                aspect="video"
                fallbackLabel=""
              />
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
{/if}

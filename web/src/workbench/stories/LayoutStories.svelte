<script lang="ts">
  import Container from "@/ui/layout/Container.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const boxes = ["Copper", "Iris", "Extender", "Flush"];
  const box = "rounded-xs border border-line-strong bg-surface-muted px-3 py-2 text-sm";
</script>

<Story
  name="Layout"
  source="layout/Layout"
  description="Stack, cluster, split, grid, and center, each with a default gap."
>
  <Variant label="stack">
    <Layout pattern="stack" gap="xs">
      {#each boxes as name (name)}<div class={box}>{name}</div>{/each}
    </Layout>
  </Variant>
  <Variant label="cluster">
    <Layout pattern="cluster">
      {#each boxes as name (name)}<div class={box}>{name}</div>{/each}
    </Layout>
  </Variant>
  <Variant label="split">
    <Layout pattern="split">
      <div class={box}>Title</div>
      <div class={box}>Actions</div>
    </Layout>
  </Variant>
  <Variant label="grid, min tile">
    <Layout as="ul" pattern="grid" min="tile">
      {#each boxes as name (name)}<li class={box}>{name}</li>{/each}
    </Layout>
  </Variant>
  <Variant label="center">
    <Layout pattern="center" class="h-24 rounded-sm border border-dashed border-line">
      <div class={box}>Centered</div>
    </Layout>
  </Variant>
</Story>

<Story
  name="Container"
  source="layout/Container"
  description="Centers content at a readable width with the page gutter."
>
  {#each ["prose", "form", "content"] as const as width (width)}
    <Variant label={width}>
      <Container {width} class="rounded-sm border border-dashed border-line py-2">
        <div class={box}>{width}</div>
      </Container>
    </Variant>
  {/each}
</Story>

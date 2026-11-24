<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Container from "@/ui/layout/Container.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const boxes = [m.storyCopper(), m.storyIris(), m.storyExtender(), m.storyFlushLabel()];
  const box = "rounded-xs border border-line-strong bg-surface-muted px-3 py-2 text-sm";
</script>

<Story name="Layout" source="layout/Layout" description={m.storyLayoutDescription()}>
  <Variant label={m.storyStack()}>
    <Layout pattern="stack" gap="xs">
      {#each boxes as name (name)}<div class={box}>{name}</div>{/each}
    </Layout>
  </Variant>
  <Variant label={m.storyCluster()}>
    <Layout pattern="cluster">
      {#each boxes as name (name)}<div class={box}>{name}</div>{/each}
    </Layout>
  </Variant>
  <Variant label={m.storySplit()}>
    <Layout pattern="split">
      <div class={box}>{m.storyTitle()}</div>
      <div class={box}>{m.storyActions()}</div>
    </Layout>
  </Variant>
  <Variant label={m.storyGridMinTile()}>
    <Layout as="ul" pattern="grid" min="tile">
      {#each boxes as name (name)}<li class={box}>{name}</li>{/each}
    </Layout>
  </Variant>
  <Variant label={m.storyCenter()}>
    <Layout pattern="center" class="h-24 rounded-sm border border-dashed border-line">
      <div class={box}>{m.storyCentered()}</div>
    </Layout>
  </Variant>
</Story>

<Story name="Container" source="layout/Container" description={m.storyContainerDescription()}>
  {#each ["prose", "form", "content"] as const as width (width)}
    <Variant label={width}>
      <Container {width} class="rounded-sm border border-dashed border-line py-2">
        <div class={box}>{width}</div>
      </Container>
    </Variant>
  {/each}
</Story>

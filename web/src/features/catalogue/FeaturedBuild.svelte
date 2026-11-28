<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import Heading from "@/ui/content/Heading.svelte";
  import Link from "@/ui/content/Link.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import BuildFacts from "./BuildFacts.svelte";
  import BuildPreview from "./BuildPreview.svelte";

  let { build }: { build: PublicBuildSummary } = $props();
</script>

<article class="@container">
  <Layout pattern="grid" class="@2xl:grid-cols-2" min="card-wide" align="center">
    <BuildPreview {build} priority />
    <Layout pattern="stack" gap="sm">
      <Heading level={2}
        ><Link href={localizePath(`/builds/${build.id}`)}>{build.display_name ?? build.title}</Link
        ></Heading
      >
      <BuildFacts {build} />
    </Layout>
  </Layout>
</article>

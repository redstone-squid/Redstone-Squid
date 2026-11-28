<script lang="ts">
  import Pencil from "@lucide/svelte/icons/pencil";
  import { groupBy } from "es-toolkit";
  import type { BuildDetail, CreditRole } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import { buildStatusLabel } from "@/locales/vocabulary.gen";
  import Button from "@/ui/actions/Button.svelte";
  import Link from "@/ui/content/Link.svelte";
  import Text from "@/ui/content/Text.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Gallery from "@/ui/data/Gallery.svelte";
  import List from "@/ui/data/List.svelte";
  import ListItem from "@/ui/data/ListItem.svelte";
  import StatGroup from "@/ui/data/StatGroup.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Chip from "@/ui/feedback/Chip.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import BuildPreview from "./BuildPreview.svelte";
  import CreatorLinks from "./CreatorLinks.svelte";
  import RecordRow from "./RecordRow.svelte";
  import SchematicViewer from "./SchematicViewer.svelte";
  import {
    buildName,
    buildSize,
    buildVolume,
    categoryLabel,
    creditRoleLabel,
    previewUrl,
    ticks,
  } from "./presentation";

  /**
   * A build's page: its media, headline timings and size, the records it holds and held,
   * its specifications, credits, downloads, and the server that sponsors it. With
   * `schematic`, the preview opens in the 3D viewer. The edit form posts to `/submit`.
   */
  type Props = {
    build: BuildDetail;
    schematic?: { url: string; resourcePackUrl: string };
  };
  let { build, schematic }: Props = $props();
  const title = $derived(buildName(build));
  const details = $derived(build.details);
  const gallery = $derived(
    build.media.flatMap((media) => {
      const src = media.kind === "video" ? null : previewUrl(media);
      if (src === null) return [];
      return [
        {
          kind: "image" as const,
          src,
          thumb: previewUrl({ url: media.thumbnail_url ?? media.url }) ?? src,
          width: media.width ?? 1600,
          height: media.height ?? 1000,
          alt: media.alt ?? m.buildPreviewAlt({ title }),
        },
      ];
    }),
  );
  const timings = $derived.by(() => {
    const items = [
      { label: m.buildOpening(), value: build.opening_time },
      { label: m.buildClosing(), value: build.closing_time },
    ];
    if ("visible_opening_time" in details) {
      items.push(
        { label: m.buildVisibleOpening(), value: details.visible_opening_time },
        { label: m.buildVisibleClosing(), value: details.visible_closing_time },
      );
    }
    if ("extension_time" in details) {
      items.push(
        { label: m.buildExtension(), value: details.extension_time },
        { label: m.buildRetraction(), value: details.retraction_time },
      );
    }
    return items
      .filter((item) => item.value !== null)
      .map((item) => ({ label: item.label, value: ticks(item.value) }));
  });
  const volume = $derived(buildVolume(build.dimensions));
  const headline = $derived([
    ...timings,
    { label: m.buildDimensions(), value: buildSize(build.dimensions) },
    ...(volume === null
      ? []
      : [{ label: m.catalogueVolume(), value: m.buildBlocks({ count: volume }) }]),
  ]);
  const specifications = $derived([
    ...("door_dimensions" in details
      ? [{ term: m.buildDoorSize(), value: buildSize(details.door_dimensions), numeric: true }]
      : []),
    ...("extension_length" in details && details.extension_length !== null
      ? [{ term: m.buildExtensionLength(), value: String(details.extension_length), numeric: true }]
      : []),
    ...("extender_type" in details && details.extender_type !== null
      ? [{ term: m.buildExtenderType(), value: details.extender_type }]
      : []),
    ...("orientation" in details && details.orientation !== null
      ? [{ term: m.buildOrientation(), value: details.orientation }]
      : []),
    ...("patterns" in details && details.patterns.length > 0
      ? [{ term: m.buildPatterns(), value: formatList(details.patterns) }]
      : []),
    ...(build.version_spec !== null || build.versions.length > 0
      ? [{ term: m.recordVersionScope(), value: build.version_spec ?? formatList(build.versions) }]
      : []),
    // Plain creators are already named under the title; other roles are listed here.
    ...Object.entries(groupBy(build.credits ?? [], (credit) => credit.role))
      .filter(([role]) => role !== "creator")
      .map(([role, credits]) => ({
        term: creditRoleLabel(role as CreditRole),
        value: formatList(credits.map((credit) => credit.name)),
      })),
    ...Object.entries(build.restrictions)
      .filter(([, values]) => values.length > 0)
      .map(([group, values]) => ({
        term: m.buildRestrictions({ group: group.replaceAll("_", " ") }),
        value: formatList(values),
      })),
  ]);
  const records = $derived([
    ...(build.records_held ?? []).map((record) => ({ record, former: false })),
    ...(build.records_former ?? []).map((record) => ({ record, former: true })),
  ]);
  // Links are untrusted input: only HTTPS ones are offered, named by their file.
  const external = (url: string) =>
    URL.canParse(url) && url.startsWith("https://") ? [new URL(url)] : [];
  const downloads = $derived(
    [...build.links.world_downloads, ...build.links.schematics].flatMap(external),
  );
  const videos = $derived(
    build.media.filter((media) => media.kind === "video").flatMap((media) => external(media.url)),
  );
  const fileName = (url: URL) => {
    const name = url.pathname.split("/").at(-1) ?? "";
    return name === "" ? url.host : name;
  };
  const website = $derived(external(build.sponsor?.website_url ?? "")[0]);
  const history = $derived([
    ...(build.submitted_at != null
      ? [{ term: m.buildSubmitted(), value: m.catalogueDate({ date: build.submitted_at }) }]
      : []),
    ...(build.approved_at != null
      ? [{ term: m.buildApproved(), value: m.catalogueDate({ date: build.approved_at }) }]
      : []),
    ...(build.last_edited
      ? [{ term: m.buildLastEdited(), value: m.catalogueDate({ date: build.last_edited.at }) }]
      : []),
  ]);
</script>

<Layout pattern="stack" gap="xl">
  <PageHeader
    {title}
    eyebrow={categoryLabel(build.category)}
    description={build.subtitle ?? undefined}
  >
    {#snippet meta()}
      {#if build.status !== "approved"}
        <Badge tone={build.status === "rejected" ? "danger" : "warning"} dot>
          {buildStatusLabel(build.status === "unknown" ? "pending" : build.status)}
        </Badge>
      {/if}
      {#if (build.records_held ?? []).length > 0}
        <Badge tone="success"
          >{m.buildRecordsHeldCount({ count: (build.records_held ?? []).length })}</Badge
        >
      {/if}
    {/snippet}
    {#snippet actions()}
      {#if build.viewer && build.viewer.edit_mode !== "unavailable"}
        <form method="post" action={localizePath("/submit")}>
          <input type="hidden" name="source_build_id" value={build.id} />
          <input type="hidden" name="category" value={build.category.toLowerCase()} />
          <Button type="submit" icon={Pencil}>
            {build.viewer.edit_mode === "propose" ? m.submissionEdit() : m.submissionEditBuild()}
          </Button>
        </form>
      {/if}
    {/snippet}
  </PageHeader>
  {#if build.creators.length > 0}<CreatorLinks creators={build.creators} />{/if}
  <Layout pattern="grid" min="card-wide" gap="xl" class="@container">
    <div class="min-w-0">
      {#if schematic}
        <SchematicViewer
          {title}
          schematicUrl={schematic.url}
          previewUrl={previewUrl(build.preview)}
          resourcePackUrl={schematic.resourcePackUrl}
        />
      {:else if gallery.length > 1}
        <Gallery
          items={gallery}
          labels={{
            previous: m.commonPrevious(),
            next: m.commonNext(),
            close: m.catalogueGalleryClose(),
            openItem: m.catalogueGalleryOpen(),
            fallback: m.buildImageUnavailable(),
            position: (current, total) => m.catalogueGalleryPosition({ current, total }),
          }}
        />
      {:else}
        <BuildPreview {build} priority class="rounded-md border border-line" />
      {/if}
    </div>
    <Layout pattern="stack" gap="lg">
      <StatGroup layout="inline" items={headline} />
      {#if build.description}<Text>{build.description}</Text>{/if}
      {#if build.tags.length > 0}
        <Layout as="ul" pattern="cluster" gap="xs" aria-label={m.buildTags()}>
          {#each build.tags as tag (tag.key)}
            <li>
              <Chip size="sm">
                {tag.value === null || typeof tag.value === "boolean"
                  ? tag.name
                  : m.buildTagValue({
                      name: tag.name,
                      value: tag.unit === null ? tag.value : `${tag.value} ${tag.unit}`,
                    })}
              </Chip>
            </li>
          {/each}
        </Layout>
      {/if}
    </Layout>
  </Layout>
  {#if records.length > 0}
    <Section title={m.buildRecords()}>
      <List>
        {#each records as { record, former } (record.record_id)}
          <RecordRow {record} {former} />
        {/each}
      </List>
    </Section>
  {/if}
  {#if specifications.length > 0}
    <Section title={m.buildSpecifications()}>
      <DescriptionList layout="grid" items={specifications} />
    </Section>
  {/if}
  {#if downloads.length > 0 || videos.length > 0}
    <Section title={m.buildDownloads()}>
      <List>
        {#each downloads as url (url.href)}
          <ListItem
            title={fileName(url)}
            href={url.href}
            description={url.host}
            density="compact"
          />
        {/each}
        {#each videos as url (url.href)}
          <ListItem
            title={m.buildVideo()}
            href={url.href}
            description={url.host}
            density="compact"
          />
        {/each}
      </List>
    </Section>
  {/if}
  {#if build.sponsor}
    <Section title={m.buildSponsor()} surface>
      <Layout pattern="stack" gap="xs">
        <Text weight="semibold">{build.sponsor.display_name ?? build.sponsor.address ?? ""}</Text>
        {#if build.sponsor.address}<Text size="sm" tone="muted">{build.sponsor.address}</Text>{/if}
        {#if build.sponsor.description}<Text size="sm">{build.sponsor.description}</Text>{/if}
        {#if website}
          <Link href={website.href} variant="standalone" externalLabel={m.catalogueExternal()}>
            {website.host}
          </Link>
        {/if}
      </Layout>
    </Section>
  {/if}
  {#if history.length > 0}
    <DescriptionList layout="inline" size="sm" items={history} />
  {/if}
</Layout>

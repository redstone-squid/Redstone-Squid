<script lang="ts">
  import type { PublishedRecordResult } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import { recordCriterionLabel, versionScopeLabel } from "@/locales/vocabulary.gen";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Layout from "@/ui/layout/Layout.svelte";

  /**
   * A record as a card linking to its page. The title already names the build kind, and
   * the version scope shows only when it narrows the record to the current version.
   */
  type Props = {
    record: PublishedRecordResult;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
  };
  let { record, as = "article", headingLevel = 3 }: Props = $props();
  const facts = $derived([
    m.recordHolderCount({ count: record.holder_build_ids.length }),
    ...(record.version_scope === "up_to_date" ? [versionScopeLabel(record.version_scope)] : []),
  ]);
</script>

<Card
  {as}
  title={record.title}
  href={localizePath(`/records/${record.record_id}`)}
  {headingLevel}
  eyebrow={recordCriterionLabel(record.criterion)}
>
  {#if record.subtitle}<Text size="sm" tone="muted">{record.subtitle}</Text>{/if}
  <Layout pattern="cluster" gap="sm" align="center">
    <MetaList items={facts} />
    {#if record.refresh_pending === true}
      <Badge tone="info" dot>{m.recordRefreshPending()}</Badge>
    {/if}
  </Layout>
</Card>

<script lang="ts">
  import type { PublishedRecordResult } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import {
    buildKindLabel,
    recordCriterionLabel,
    versionScopeLabel,
  } from "@/locales/vocabulary.gen";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import { recordFreshness } from "./freshness";

  /** A record as a card linking to its page. `headingLevel` is 2 directly under a page title. */
  type Props = {
    record: PublishedRecordResult;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
  };
  let { record, as = "article", headingLevel = 3 }: Props = $props();
</script>

<Card
  {as}
  title={record.title}
  href={localizePath(`/records/${record.record_id}`)}
  {headingLevel}
  eyebrow={recordCriterionLabel(record.criterion)}
>
  {#if record.subtitle}<Text size="sm" tone="muted">{record.subtitle}</Text>{/if}
  <MetaList
    items={[
      buildKindLabel(record.build_kind),
      versionScopeLabel(record.version_scope),
      m.recordHolderCount({ count: record.holder_build_ids.length }),
      ...recordFreshness(record),
    ]}
  />
</Card>

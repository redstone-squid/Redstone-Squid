<script lang="ts">
  import type { RecordCriterion, RecordHolderPeriod } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import Timeline from "@/ui/data/Timeline.svelte";
  import { buildHref, buildName, recordMeasurements } from "./presentation";

  /**
   * Who held a record and when, newest first, with the number each holder set. A period
   * names its builds when the API sends them and falls back to build numbers otherwise.
   */
  type Props = { history: RecordHolderPeriod[]; criterion: RecordCriterion; label: string };
  let { history, criterion, label }: Props = $props();
  const items = $derived(
    history.toReversed().map((period) => {
      const names =
        period.holders?.map((holder) => buildName(holder)) ??
        period.build_ids.map((id) => m.recordHistoryBuild({ id }));
      const [headline] = recordMeasurements({ criterion, metrics: period.metrics });
      const [only] = period.build_ids;
      return {
        id: period.held_from,
        datetime: period.held_from,
        dateLabel:
          period.held_until == null
            ? m.recordHistorySince({ from: period.held_from })
            : m.recordHistoryRange({ from: period.held_from, until: period.held_until }),
        title: formatList(names),
        href: period.build_ids.length === 1 && only !== undefined ? buildHref(only) : undefined,
        description:
          headline === undefined
            ? undefined
            : m.recordHistoryMetric({ label: headline.label, value: headline.value }),
        tone: period.held_until == null ? ("accent" as const) : undefined,
      };
    }),
  );
</script>

<Timeline {items} {label} />

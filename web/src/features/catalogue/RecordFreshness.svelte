<script lang="ts">
  import type { PublishedRecordResult } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";

  /** When a record was last confirmed, and whether a recomputation is queued. */
  let { record }: { record: PublishedRecordResult } = $props();
  const checked = $derived(
    record.last_checked_at != null
      ? m.recordLastChecked({ date: record.last_checked_at })
      : m.recordComputed({ date: record.computed_at }),
  );
</script>

{[checked, record.refresh_pending === true ? m.recordRefreshPending() : null]
  .filter((part) => part !== null)
  .join(" · ")}

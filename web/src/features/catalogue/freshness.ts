import type { PublishedRecordResult } from "@/generated/types.gen";
import * as m from "@/locales/messages.gen";

/** When a record was last confirmed, and whether a recomputation is queued, as `MetaList` facts. */
export function recordFreshness(record: PublishedRecordResult): string[] {
  const checked =
    record.last_checked_at != null
      ? m.recordLastChecked({ date: record.last_checked_at })
      : m.recordComputed({ date: record.computed_at });
  return record.refresh_pending === true ? [checked, m.recordRefreshPending()] : [checked];
}

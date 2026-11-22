import type { BuildKind, RecordCriterion, VersionScope } from "../../generated/types.gen";
import * as m from "../../locales/messages";

export const criterionLabels = {
  first: m.taxonomyFirst,
  fastest: m.taxonomyFastest,
  smallest: m.taxonomySmallest,
  fastest_smallest: m.taxonomyFastestSmallest,
  smallest_fastest: m.taxonomySmallestFastest,
} as const satisfies Record<RecordCriterion, unknown>;

export const buildKindLabels = {
  door: m.taxonomyDoor,
  entrance: m.taxonomyEntrance,
  extender: m.taxonomyExtender,
  utility: m.taxonomyUtility,
} as const satisfies Record<BuildKind, unknown>;

export const versionScopeLabels = {
  all_time: m.taxonomyAllTime,
  up_to_date: m.taxonomyUpToDate,
} as const satisfies Record<VersionScope, unknown>;

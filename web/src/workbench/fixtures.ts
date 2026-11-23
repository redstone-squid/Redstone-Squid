import type { PublicBuildSummary, PublishedRecordResult } from "@/generated/types.gen";

function build(
  id: number,
  title: string,
  creators: string[],
  [width, height, depth]: [number, number, number],
  opening: number | null,
  preview: string | null,
): PublicBuildSummary {
  return {
    id,
    revision: 1,
    title,
    display_name: null,
    status: "confirmed",
    category: "door",
    dimensions: { width, height, depth },
    creators,
    tags: [],
    preview: preview === null ? null : { kind: "image", url: preview },
    version_spec: null,
    versions: [],
    opening_time: opening,
    closing_time: null,
    created_at: "2026-01-12T00:00:00Z",
    updated_at: null,
  };
}

/** Builds for the production cards. Illustrations, not real schematics. */
export const buildFixtures: PublicBuildSummary[] = [
  build(1, "Copper Bolt 5×5", ["CircuitSage", "红石猫"], [9, 7, 4], 8, "/fixtures/copper-door.svg"),
  build(
    2,
    "Observerless Iris / 无侦测器全隐藏虹膜门",
    ["Space Builder"],
    [12, 12, 5],
    12,
    "/fixtures/iris-door.svg",
  ),
  build(3, "Slim Piston Extender", ["CircuitSage"], [3, 5, 8], null, null),
];

export const recordFixtures: PublishedRecordResult[] = [
  {
    result_id: 1,
    definition_id: 1,
    record_id: "fastest-3x3-flush",
    title: "Fastest 3×3 flush piston door",
    subtitle: "Full-flush, seamless",
    criterion: "fastest",
    build_kind: "door",
    version_scope: "all_time",
    status: "resolved",
    holder_build_ids: [1],
    computed_at: "2026-01-12T00:00:00Z",
  },
  {
    result_id: 2,
    definition_id: 2,
    record_id: "smallest-iris",
    title: "Smallest iris door",
    subtitle: null,
    criterion: "smallest",
    build_kind: "door",
    version_scope: "up_to_date",
    status: "unresolved",
    holder_build_ids: [1, 2],
    computed_at: "2026-01-12T00:00:00Z",
    refresh_pending: true,
  },
];

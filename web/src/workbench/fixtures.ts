import * as m from "@/locales/messages.gen";
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
    status: "approved",
    category: "Door",
    dimensions: { width, height, depth },
    creators: creators.map((name) => ({ name, creator_id: null })),
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
export function buildFixtures(): PublicBuildSummary[] {
  return [
    build(
      1,
      m.storyCopperBolt55(),
      ["CircuitSage", "红石猫"],
      [9, 7, 4],
      8,
      "/fixtures/copper-door.svg",
    ),
    build(
      2,
      m.storyObserverlessIris(),
      ["Space Builder"],
      [12, 12, 5],
      12,
      "/fixtures/iris-door.svg",
    ),
    build(3, m.storySlimExtender(), ["CircuitSage"], [3, 5, 8], null, null),
  ];
}

export function recordFixtures(): PublishedRecordResult[] {
  return [
    {
      result_id: 1,
      definition_id: 1,
      record_id: "fastest-3x3-flush",
      title: m.storyFastestFlushDoor(),
      subtitle: m.storyFullFlushSeamless(),
      criterion: "fastest",
      build_kind: "door",
      version_scope: "all_time",
      holder_build_ids: [1],
      computed_at: "2026-01-12T00:00:00Z",
    },
    {
      result_id: 2,
      definition_id: 2,
      record_id: "smallest-iris",
      title: m.storySmallestIris(),
      subtitle: null,
      criterion: "smallest",
      build_kind: "door",
      version_scope: "up_to_date",
      holder_build_ids: [1, 2],
      tied_build_ids: [3],
      computed_at: "2026-01-12T00:00:00Z",
      refresh_pending: true,
    },
  ];
}

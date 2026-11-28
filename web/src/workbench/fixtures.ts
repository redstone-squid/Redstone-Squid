import { omit } from "es-toolkit";
import * as m from "@/locales/messages.gen";
import type {
  CandidateGap,
  CreatorStats,
  PublicBuildCredit,
  PublicBuildSummary,
  PublicRecordDetail,
  PublicRecordSummary,
  RecordCriterion,
  RecordHolderPeriod,
} from "@/generated/types.gen";

const creators = (...names: string[]) => names.map((name) => ({ name, account_id: null }));

function build(
  id: number,
  title: string,
  overrides: Partial<PublicBuildSummary> = {},
): PublicBuildSummary {
  return {
    id,
    revision: 1,
    title,
    display_name: null,
    status: "approved",
    category: "Door",
    dimensions: { width: 9, height: 7, depth: 4 },
    creators: [],
    tags: [],
    preview: null,
    version_spec: null,
    versions: [],
    opening_time: 8,
    closing_time: null,
    created_at: "2026-01-12T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

const image = (url: string) => ({ kind: "image" as const, url });

/** Builds for the production cards. Illustrations, not real schematics. */
export function buildFixtures(): PublicBuildSummary[] {
  return [
    build(1, m.storyCopperBolt55(), {
      creators: creators("CircuitSage", "红石猫"),
      preview: image("/fixtures/copper-door.svg"),
    }),
    build(2, m.storyObserverlessIris(), {
      creators: creators("Space Builder"),
      dimensions: { width: 12, height: 12, depth: 5 },
      opening_time: 12,
      preview: image("/fixtures/iris-door.svg"),
    }),
    build(3, m.storySlimExtender(), {
      creators: creators("CircuitSage"),
      dimensions: { width: 3, height: 5, depth: 8 },
      opening_time: null,
    }),
  ];
}

/** One build per way the card and its parts can look. */
export function buildScenarios() {
  const [standard, long, noPreview] = buildFixtures() as [
    PublicBuildSummary,
    PublicBuildSummary,
    PublicBuildSummary,
  ];
  return {
    standard,
    noPreview,
    unavailableImage: {
      ...standard,
      id: 4,
      preview: image("https://media.fixture.invalid/broken.png"),
    },
    unknownMeasurements: build(5, m.storySlimExtender(), {
      creators: creators("CircuitSage"),
      dimensions: { width: null, height: 5, depth: null },
      opening_time: null,
      preview: image("/fixtures/copper-door.svg"),
    }),
    crowded: {
      ...long,
      id: 6,
      creators: creators(
        "Space Builder",
        "CircuitSage",
        "红石猫",
        "PistonPioneer",
        "FlushFanatic",
        "TorchTinkerer",
      ),
    },
  };
}

function record(
  id: number,
  title: string,
  overrides: Partial<PublicRecordSummary> = {},
): PublicRecordSummary {
  const holderIds = overrides.holder_build_ids ?? [1];
  return {
    result_id: id,
    definition_id: id,
    record_id: `story-record-${id}`,
    title,
    subtitle: null,
    criterion: "fastest",
    build_kind: "door",
    version_scope: "all_time",
    category_key: `door:story-${id}`,
    status: "resolved",
    holder_build_ids: holderIds,
    holders: holderIds.map((buildId) => ({
      build_id: buildId,
      title,
      creators: [],
      preview: null,
    })),
    computed_at: "2026-01-12T00:00:00Z",
    ...overrides,
  };
}

export function recordFixtures(): PublicRecordSummary[] {
  return [
    record(1, m.storyFastestFlushDoor(), {
      record_id: "fastest-3x3-flush",
      subtitle: m.storyFullFlushSeamless(),
    }),
    record(2, m.storySmallestIris(), {
      record_id: "smallest-iris",
      criterion: "smallest",
      version_scope: "up_to_date",
      status: "unresolved",
      holder_build_ids: [1, 2],
      tied_build_ids: [3],
      refresh_pending: true,
    }),
  ];
}

/** Record cards for each state the card calls out. */
export function recordScenarios() {
  const title = m.storyFastestFlushDoor();
  return {
    single: record(10, title, { subtitle: m.storyFullFlushSeamless() }),
    noSubtitle: record(11, title),
    shared: record(12, title, { holder_build_ids: [1, 2] }),
    tied: record(13, title, { tied_build_ids: [3] }),
    currentVersion: record(14, title, { version_scope: "up_to_date" }),
    refreshPending: record(15, title, { refresh_pending: true }),
    everything: record(16, title, {
      subtitle: m.storyFullFlushSeamless(),
      holder_build_ids: [1, 2],
      tied_build_ids: [3],
      version_scope: "up_to_date",
      refresh_pending: true,
    }),
  };
}

const criteria: RecordCriterion[] = [
  "first",
  "fastest",
  "smallest",
  "fastest_smallest",
  "smallest_fastest",
];
export const recordPerCriterion = () =>
  criteria.map((criterion, index) => record(20 + index, m.storyFastestFlushDoor(), { criterion }));

function detail(
  summary: PublicRecordSummary,
  holders: PublicBuildSummary[],
  overrides: Partial<PublicRecordDetail> = {},
): PublicRecordDetail {
  return {
    ...omit(summary, ["holders"]),
    holder_build_ids: holders.map(({ id }) => id),
    holder_builds: holders,
    last_checked_at: "2026-02-03T00:00:00Z",
    ...overrides,
  };
}

const gap = (build_id: number, field: CandidateGap["field"]): CandidateGap => ({
  build_id,
  field,
});

const history: RecordHolderPeriod[] = [
  { build_ids: [3], held_from: "2025-03-01T00:00:00Z", held_until: "2025-09-14T00:00:00Z" },
  { build_ids: [1, 2], held_from: "2025-09-14T00:00:00Z", held_until: "2026-01-12T00:00:00Z" },
  { build_ids: [1], held_from: "2026-01-12T00:00:00Z", held_until: null },
];

/** Whole record pages, one per layout the page can take. */
export function recordDetailScenarios() {
  const { standard, noPreview, unknownMeasurements, crowded } = buildScenarios();
  const title = m.storyFastestFlushDoor();
  const base = (id: number, overrides: Partial<PublicRecordSummary> = {}) =>
    record(id, title, { subtitle: m.storyFullFlushSeamless(), ...overrides });
  return {
    single: detail(base(30), [standard], { history }),
    shared: detail(base(31), [standard, crowded]),
    sharedUndated: detail(base(32), [standard, unknownMeasurements], {
      status: "unresolved",
      gaps: [gap(unknownMeasurements.id, "completion_at")],
    }),
    changeable: detail(base(33, { refresh_pending: true }), [standard], {
      tied_builds: [noPreview],
      contender_builds: [unknownMeasurements],
      gaps: [gap(unknownMeasurements.id, "opening"), gap(unknownMeasurements.id, "closing")],
    }),
    retired: detail(base(34), [], { retired_at: "2026-02-03T00:00:00Z", history }),
    noHolders: detail(base(35), []),
  };
}

const credit = (name: string, creditId: string, nameId: number): PublicBuildCredit => ({
  name,
  kind: "name",
  username: null,
  role: "creator",
  attribution: "explicit",
  account_id: null,
  credit_id: creditId,
  name_id: nameId,
});

/** A creator's page: builds with credits, a yearly count, and the records they hold. */
export function creatorFixtures() {
  const [copper, iris, slim] = buildFixtures() as [
    PublicBuildSummary,
    PublicBuildSummary,
    PublicBuildSummary,
  ];
  const builds = [
    { ...copper, credits: [credit("CircuitSage", "c1", 7), credit("红石猫", "c2", 8)] },
    { ...iris, credits: [credit("CircuitSage", "c3", 7)] },
    { ...slim, credits: [credit("CircuitSage", "c4", 7)] },
  ];
  const stats: CreatorStats = {
    build_count: builds.length,
    builds_by_year: [
      { year: 2026, count: 2 },
      { year: 2025, count: 1 },
    ],
    records_held: [
      { id: "fastest-3x3-flush", title: m.storyFastestFlushDoor() },
      { id: "smallest-iris", title: m.storySmallestIris() },
    ],
    records_ever: 4,
  };
  const newcomer: CreatorStats = {
    build_count: 0,
    builds_by_year: [],
    records_held: [],
    records_ever: 0,
  };
  return { builds, stats, newcomer, claimNameId: 7 };
}

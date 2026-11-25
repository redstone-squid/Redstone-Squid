import type { DoorRecordEvidence } from "@/generated/types.gen";

export const doorEvidence: DoorRecordEvidence = {
  evaluation: {
    opening: [6, 3, 1],
    criterion: "smallest",
    version_id: null,
    evaluated_at: "2026-08-10T12:00:00Z",
    resolution: { status: "resolved", holder_ids: [1] },
    holder_configuration_ids: [0],
    configurations: [
      {
        id: 0,
        build_id: 1,
        branch_index: 0,
        membership: "supported",
        parameters: [["n", 6]],
        volume: 30,
        timing_variants: [[3, null, null, null, null, null]],
      },
    ],
  },
  sources: [
    {
      build_id: 1,
      expansion: {
        parameters: [
          {
            name: "n",
            minimum: 1,
            maximum_kind: "unbounded",
            initial_steps: [],
            repeating_steps: [1],
          },
        ],
        branches: [
          {
            opening_width: "n",
            opening_height: "3",
            opening_depth: "1",
            volume: "10ceil(n/2)",
            timings: [{ method: "opening", expression: "floor(n/2)", unit: "game_tick" }],
            evidence: [
              {
                source_message_id: 42,
                text: "Add one width layer; opening takes floor(n/2) game ticks.",
              },
            ],
          },
        ],
      },
    },
  ],
};

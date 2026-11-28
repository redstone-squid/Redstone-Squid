import * as m from "@/locales/messages.gen";
import type {
  PredictedRecord,
  RecordPreview,
  SubmissionDetail,
  VoteSessionDetail,
} from "@/generated/types.gen";

const facts = () => ({
  title: m.storyCopperBolt55(),
  dimensions: { width: 9, height: 7, depth: 4 },
  creators: ["CircuitSage", "红石猫"],
  opening_time: 8,
});

function submission(
  overrides: Partial<SubmissionDetail["submission"]> = {},
  detail: Partial<SubmissionDetail> = {},
): SubmissionDetail {
  return {
    submission: {
      id: "story-submission",
      build_id: 1,
      title: m.storyCopperBolt55(),
      kind: "new_build",
      status: "pending",
      submitted_at: "2026-01-12T00:00:00Z",
      published: false,
      decided_revision: null,
      ...overrides,
    },
    base_revision: null,
    vote_session_id: null,
    facts: facts(),
    review_revision: null,
    reviewed_facts: {},
    ...detail,
  };
}

function vote(overrides: Partial<VoteSessionDetail> = {}): VoteSessionDetail {
  return {
    id: 1,
    kind: "build",
    status: "open",
    result: "pending",
    pass_threshold: 3,
    fail_threshold: -3,
    build_id: 1,
    options: [
      { id: "up", label: null, choice: "up", multiplier: 1, position: 0 },
      { id: "down", label: null, choice: "down", multiplier: -1, position: 1 },
    ],
    tallies: {
      raw: { up: 2, down: 1 },
      weighted: { up: 2, down: 1 },
      upvotes: 2,
      downvotes: 1,
      net: 1,
    },
    poll: null,
    own_selection: null,
    voters: null,
    viewer: { eligible: true, weight: 1 },
    ...overrides,
  };
}

/** A submission as each kind of visitor sees it, with the review vote it links to, if any. */
export function submissionScenarios() {
  return {
    reviewer: {
      detail: submission(
        {},
        { vote_session_id: 1, review_revision: 2, can_approve: true, can_reject: true },
      ),
      vote: vote(),
    },
    voted: {
      detail: submission({ kind: "edit" }, { vote_session_id: 1 }),
      vote: vote({ own_selection: { option_id: "up" } }),
    },
    submitter: { detail: submission(), vote: null },
    published: {
      detail: submission(
        { status: "approved", published: true, decided_revision: 2 },
        { review_revision: 2, reviewed_facts: { ...facts(), opening_time: 9 } },
      ),
      vote: null,
    },
    rejected: {
      detail: submission({ status: "rejected" }, { feedback: m.storyAFastFlush55Door() }),
      vote: null,
    },
  };
}

function predicted(
  title: string,
  outcome: PredictedRecord["outcome"],
  overrides: Partial<PredictedRecord> = {},
): PredictedRecord {
  return {
    record_id: null,
    category_key: title,
    title,
    criterion: "fastest",
    scope: "all_time",
    version_id: null,
    outcome,
    missing: [],
    current_holders: [{ build_id: 2, title: m.storyObserverlessIris(), deltas: [] }],
    ...overrides,
  };
}

/** What a submission would do to the records: every outcome, unknown facts, and no categories. */
export function predictionScenarios(): Record<
  "outcomes" | "missingFacts" | "empty",
  RecordPreview
> {
  const evaluated_at = "2026-01-12T09:30:00Z";
  return {
    outcomes: {
      revision: 2,
      evaluated_at,
      missing_fields: [],
      records: [
        predicted(m.storyFastestFlushDoor(), "holds", {
          current_holders: [
            {
              build_id: 2,
              title: m.storyObserverlessIris(),
              deltas: [
                {
                  metric: "opening_time",
                  unit: "game_ticks",
                  candidate: 8,
                  holder: 12,
                  difference: -4,
                },
              ],
            },
          ],
        }),
        predicted(m.storySmallestIris(), "shares", { scope: "up_to_date" }),
        predicted(m.storySlimExtender(), "ties_later"),
        predicted(m.storyCopperBolt55(), "loses", {
          current_holders: [
            {
              build_id: 2,
              title: null,
              deltas: [
                { metric: "volume", unit: "blocks", candidate: 252, holder: 180, difference: 72 },
              ],
            },
          ],
        }),
      ],
    },
    missingFacts: {
      revision: null,
      evaluated_at,
      missing_fields: ["opening_time", "dimensions"],
      records: [
        predicted(m.storyFastestFlushDoor(), "undecided", {
          missing: [
            { fact: "opening", fields: ["opening_time"], build_id: null },
            { fact: "completion_at", fields: ["completion_date"], build_id: 2 },
          ],
        }),
      ],
    },
    empty: { revision: null, evaluated_at, missing_fields: [], records: [] },
  };
}

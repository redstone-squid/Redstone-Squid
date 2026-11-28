import * as m from "@/locales/messages.gen";

export function statusLabel(status: string): string {
  switch (status) {
    case "approved": {
      return m.creatorClaimApproved();
    }
    case "rejected": {
      return m.creatorClaimRejected();
    }
    case "cancelled": {
      return m.submissionCancelled();
    }
    case "stale": {
      return m.submissionReviewStale();
    }
    default: {
      return m.creatorClaimPending();
    }
  }
}

/** Flatten nested measurements into labelled rows for the definition list. */
export function factRows(
  facts: Record<string, unknown>,
  parent = "",
): { term: string; value: string }[] {
  return Object.entries(facts).flatMap(([key, value]) => {
    const term = [parent, key.replaceAll("_", " ")].filter(Boolean).join(" · ");
    if (value == null) return [];
    if (Array.isArray(value) && value.every((item) => typeof item !== "object")) {
      return value.length > 0 ? [{ term, value: value.join(", ") }] : [];
    }
    if (typeof value === "object") return factRows(Object.fromEntries(Object.entries(value)), term);
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
      return [{ term, value: String(value) }];
    return [];
  });
}

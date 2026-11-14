export type GuidedSearch = {
  keywords?: string;
  creator?: string;
  version?: string;
  category?: string;
  type?: string;
  maxWidth?: string;
  maxHeight?: string;
  maxDepth?: string;
  maxOpening?: string;
  maxClosing?: string;
  tag?: string;
};

export function quoteQueryValue(value: string): string {
  return `"${value.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

function field(name: string, value?: string): string | undefined {
  const normalized = value?.trim();
  return normalized ? `${name}:${quoteQueryValue(normalized)}` : undefined;
}

function maximum(name: string, value?: string): string | undefined {
  const normalized = value?.trim();
  if (!normalized || !/^\d+(?:\.\d+)?(?:gt|rt|s)?$/i.test(normalized)) return undefined;
  return `${name}<=${normalized}`;
}

/** Quote text as literal query values and omit empty fields or invalid numeric maxima. */
export function composeGuidedQuery(input: GuidedSearch): string {
  return [
    input.keywords?.trim() ? quoteQueryValue(input.keywords.trim()) : undefined,
    field("creator", input.creator),
    field("version", input.version),
    field("kind", input.category),
    field("type", input.type),
    maximum("width", input.maxWidth),
    maximum("height", input.maxHeight),
    maximum("depth", input.maxDepth),
    maximum("opening_time", input.maxOpening),
    maximum("closing_time", input.maxClosing),
    field("tag", input.tag),
  ]
    .filter((part): part is string => Boolean(part))
    .join(" ");
}

export function creatorQuery(name: string): string {
  return `creator:${quoteQueryValue(name)}`;
}

/** Parse a nonzero decimal identifier that fits in a JavaScript safe integer. */
export function readPositiveInteger(value: string | null): number | undefined {
  if (!value || !/^[1-9]\d*$/.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : undefined;
}

export function queryFromUrl(parameters: URLSearchParams): GuidedSearch {
  const value = (name: string): string | undefined => {
    const found = parameters.get(name)?.trim();
    if (found === undefined || found === "") return undefined;
    return found;
  };
  return {
    keywords: value("keywords"),
    creator: value("creator"),
    version: value("version"),
    category: value("category"),
    type: value("type"),
    maxWidth: value("maxWidth"),
    maxHeight: value("maxHeight"),
    maxDepth: value("maxDepth"),
    maxOpening: value("maxOpening"),
    maxClosing: value("maxClosing"),
    tag: value("tag"),
  };
}

/** Guided form fields remain authoritative even before the island hydrates. */
export function searchStateFromUrl(parameters: URLSearchParams): {
  guided: GuidedSearch;
  query: string;
  mode: "guided" | "advanced";
  sort: string;
} {
  const guided = queryFromUrl(parameters);
  const requestedMode = parameters.get("mode");
  const rawQuery = parameters.get("q")?.trim() ?? "";
  const hasGuided = Object.values(guided).some(Boolean);
  const mode =
    requestedMode === "guided" || requestedMode === "advanced"
      ? requestedMode
      : rawQuery && !hasGuided
        ? "advanced"
        : "guided";
  const query =
    requestedMode === "guided"
      ? composeGuidedQuery(guided)
      : requestedMode === "advanced"
        ? rawQuery
        : rawQuery || composeGuidedQuery(guided);
  return { guided, query, mode, sort: parameters.get("sort")?.trim() ?? "" };
}

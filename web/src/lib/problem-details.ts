/** Error fields the clients expose after checking an untrusted HTTP response.
 *
 * Unknown application codes remain strings so a newer API can add codes without
 * making its otherwise valid error response unreadable to an older client.
 */
export type ApiProblemDetail = {
  title: string;
  status: number;
  type?: string;
  detail?: string | null;
  instance?: string | null;
  code?: string | null;
  resource?: string | null;
  context?: Record<string, unknown> | null;
};

/** Decode an error envelope without claiming that arbitrary extension values are typed JSON. */
export function decodeProblemDetail(value: unknown): ApiProblemDetail | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.title !== "string" ||
    typeof candidate.status !== "number" ||
    !Number.isInteger(candidate.status) ||
    candidate.status < 100 ||
    candidate.status > 599
  )
    return undefined;
  if (candidate.type !== undefined && typeof candidate.type !== "string") return undefined;
  for (const field of ["detail", "instance", "code", "resource"] as const) {
    const fieldValue = candidate[field];
    if (fieldValue !== undefined && fieldValue !== null && typeof fieldValue !== "string")
      return undefined;
  }
  if (
    candidate.context !== undefined &&
    candidate.context !== null &&
    (typeof candidate.context !== "object" || Array.isArray(candidate.context))
  )
    return undefined;
  return candidate as ApiProblemDetail;
}

import type { APIRoute } from "astro";
import { z } from "zod";
import { recordBrowserReport } from "@/server/telemetry";

const MAX_BYTES = 512;
const REPORTS_PER_MINUTE = 120;
const traceparent = z.string().regex(/^00-(?!0{32}-)[a-f0-9]{32}-(?!0{16}-)[a-f0-9]{16}-01$/);
const timing = z.int().min(0).max(600_000);
const reportSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("navigation"),
    traceparent,
    responseStart: timing,
    domComplete: timing,
  }),
  z.strictObject({ kind: z.enum(["script_error", "unhandled_rejection"]), traceparent }),
]);

let windowStart = 0;
let reports = 0;

export const POST: APIRoute = async ({ request }) => {
  if (process.env.SQUID_OBSERVABILITY_ENABLED !== "true")
    return new Response(null, { status: 404 });
  if (request.headers.get("Sec-Fetch-Site") !== "same-origin")
    return new Response(null, { status: 403 });
  if (request.headers.get("Content-Type") !== "application/json")
    return new Response(null, { status: 415 });

  const now = performance.now();
  if (now - windowStart >= 60_000) {
    windowStart = now;
    reports = 0;
  }
  if (reports >= REPORTS_PER_MINUTE) return new Response(null, { status: 429 });
  reports++;

  const reader = request.body?.getReader();
  if (reader === undefined) return new Response(null, { status: 400 });
  const bytes = new Uint8Array(MAX_BYTES);
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (length + value.length > MAX_BYTES) {
      void reader.cancel().catch(() => undefined);
      return new Response(null, { status: 413 });
    }
    bytes.set(value, length);
    length += value.length;
  }

  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(bytes.subarray(0, length)));
  } catch {
    return new Response(null, { status: 400 });
  }
  const parsed = reportSchema.safeParse(body);
  if (!parsed.success) return new Response(null, { status: 400 });
  const report = parsed.data;
  recordBrowserReport(
    report.traceparent,
    report.kind,
    report.kind === "navigation"
      ? {
          "browser.navigation.response_start_ms": report.responseStart,
          "browser.navigation.dom_complete_ms": report.domComplete,
        }
      : {},
  );
  return new Response(null, { status: 204 });
};

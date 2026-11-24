import type { APIRoute } from "astro";
import { z } from "zod";
import { ReportIntake } from "@/server/report-intake";
import { recordBrowserReport } from "@/server/telemetry";

const intake = new ReportIntake({ maxBytes: 512, perMinute: 120 });
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

export const POST: APIRoute = async ({ request }) => {
  if (process.env.SQUID_OBSERVABILITY_ENABLED !== "true")
    return new Response(null, { status: 404 });
  if (request.headers.get("Sec-Fetch-Site") !== "same-origin")
    return new Response(null, { status: 403 });
  if (request.headers.get("Content-Type") !== "application/json")
    return new Response(null, { status: 415 });

  const read = await intake.read(request);
  if ("rejection" in read) return read.rejection;
  const parsed = reportSchema.safeParse(read.body);
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

import type { APIRoute } from "astro";
import { z } from "zod";
import { ReportIntake } from "@/server/report-intake";

// Browsers batch reports, and each repeats the whole policy, so allow a few kilobytes apiece.
const intake = new ReportIntake({ maxBytes: 64 * 1024, perMinute: 60 });
const violation = z.object({
  type: z.literal("csp-violation"),
  body: z.object({
    documentURL: z.string(),
    blockedURL: z.string().optional(),
    effectiveDirective: z.string(),
    disposition: z.string(),
    sourceFile: z.string().optional(),
    lineNumber: z.int().optional(),
  }),
});
// Other report types can share the endpoint in future; skip them rather than reject the batch.
const reportsSchema = z.array(z.unknown()).max(100);

/**
 * Logs content security policy violations that browsers send through the Reporting API, so a
 * policy that blocks something in production does not fail silently. Addresses are cut to
 * their origin and path: query strings and script samples can carry visitor data.
 */
export const POST: APIRoute = async ({ request }) => {
  if (request.headers.get("Content-Type") !== "application/reports+json")
    return new Response(null, { status: 415 });
  const read = await intake.read(request);
  if ("rejection" in read) return read.rejection;
  const reports = reportsSchema.safeParse(read.body);
  if (!reports.success) return new Response(null, { status: 400 });

  for (const report of reports.data) {
    const parsed = violation.safeParse(report);
    if (!parsed.success) continue;
    const { documentURL, blockedURL, effectiveDirective, disposition, sourceFile, lineNumber } =
      parsed.data.body;
    console.warn(
      JSON.stringify({
        event: "csp_violation",
        directive: effectiveDirective,
        disposition,
        document: URL.parse(documentURL)?.pathname,
        blocked: blockedURL === undefined ? undefined : withoutQuery(blockedURL),
        source: sourceFile === undefined ? undefined : withoutQuery(sourceFile),
        line: lineNumber,
      }),
    );
  }
  return new Response(null, { status: 204 });
};

/** Keep keywords such as `inline` and `eval`; reduce addresses to origin and path. */
function withoutQuery(address: string): string {
  const url = URL.parse(address);
  if (url === null) return address.slice(0, 64);
  return url.origin === "null" ? url.protocol : `${url.origin}${url.pathname}`;
}

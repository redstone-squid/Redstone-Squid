import { ROOT_CONTEXT, SpanKind, SpanStatusCode, TraceFlags, trace } from "@opentelemetry/api";
import { NodeSDK } from "@opentelemetry/sdk-node";

if (process.env.SQUID_OBSERVABILITY_ENABLED === "true") {
  new NodeSDK({
    serviceName: process.env.OTEL_SERVICE_NAME ?? "redstone-squid-web",
    autoDetectResources: false,
    logRecordProcessors: [],
    metricReaders: [],
  }).start();
}

const tracer = trace.getTracer("redstone-squid-web");

/** Trace an HTTP operation using low-cardinality attributes and no request payloads. */
export function traceHttp(
  name: string,
  kind: SpanKind,
  attributes: Record<string, string>,
  operation: () => Promise<Response>,
): Promise<Response> {
  return tracer.startActiveSpan(name, { kind, attributes }, async (span) => {
    try {
      const response = await operation();
      span.setAttribute("http.response.status_code", response.status);
      if (response.status >= (kind === SpanKind.CLIENT ? 400 : 500)) {
        span.setStatus({ code: SpanStatusCode.ERROR });
      }
      return response;
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR });
      throw error;
    } finally {
      span.end();
    }
  });
}

/** Link a browser report to its page span without using client input as the parent. */
export function recordBrowserReport(
  traceparent: string,
  kind: "navigation" | "script_error" | "unhandled_rejection",
  timings: Record<string, number>,
): void {
  const span = tracer.startSpan(
    "catalogue browser report",
    {
      links: [
        {
          context: {
            traceId: traceparent.slice(3, 35),
            spanId: traceparent.slice(36, 52),
            traceFlags: TraceFlags.SAMPLED,
            isRemote: true,
          },
        },
      ],
      attributes: { "browser.report.kind": kind, ...timings },
    },
    ROOT_CONTEXT,
  );
  if (kind !== "navigation") span.setStatus({ code: SpanStatusCode.ERROR });
  span.end();
}
